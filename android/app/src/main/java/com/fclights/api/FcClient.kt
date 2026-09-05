package com.fclights.api

import com.fclights.model.ApiError
import com.fclights.model.BrightnessRequest
import com.fclights.model.EffectRequest
import com.fclights.model.EffectSpec
import com.fclights.model.FcJson
import com.fclights.model.Health
import com.fclights.model.Layout
import com.fclights.model.LightState
import com.fclights.model.ParamsRequest
import com.fclights.model.PowerRequest
import com.fclights.model.Scene
import com.fclights.model.SceneCreateRequest
import com.fclights.model.SceneUpdateRequest
import com.fclights.model.Status
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * A controller address. Kept as host + port rather than a URL string so the
 * WebSocket URL and the REST base URL are derived from one thing.
 */
data class Endpoint(val host: String, val port: Int = DEFAULT_PORT) {
    val baseUrl: String get() = "http://${bracketed()}:$port"
    val wsUrl: String get() = "ws://${bracketed()}:$port/api/ws"

    /** A bare IPv6 literal has to be bracketed inside a URL. */
    private fun bracketed(): String =
        if (host.contains(':') && !host.startsWith("[")) "[$host]" else host

    override fun toString(): String = if (port == DEFAULT_PORT) host else "$host:$port"

    companion object {
        const val DEFAULT_PORT = 7891

        /**
         * Parse what a user typed into the address box: `192.168.1.164`,
         * `fadecandy.local`, `fadecandy:7891`, or a full `http://...` URL.
         * Returns null if there is no usable host in it.
         */
        fun parse(text: String): Endpoint? {
            var s = text.trim()
            if (s.isEmpty()) return null
            s = s.removePrefix("http://").removePrefix("https://").removePrefix("ws://")
            s = s.substringBefore('/').trim()
            if (s.isEmpty()) return null

            // Bracketed IPv6, optionally with a port.
            if (s.startsWith("[")) {
                val close = s.indexOf(']')
                if (close < 0) return null
                val host = s.substring(1, close)
                val rest = s.substring(close + 1)
                val port = if (rest.startsWith(":")) rest.drop(1).toIntOrNull() ?: return null else DEFAULT_PORT
                return if (host.isEmpty() || port !in 1..65535) null else Endpoint(host, port)
            }

            // A bare IPv6 literal has more than one colon, and no port.
            if (s.count { it == ':' } > 1) return Endpoint(s, DEFAULT_PORT)

            val host = s.substringBefore(':')
            val portText = s.substringAfter(':', "")
            if (host.isEmpty()) return null
            val port = if (portText.isEmpty()) DEFAULT_PORT else portText.toIntOrNull() ?: return null
            if (port !in 1..65535) return null
            return Endpoint(host, port)
        }
    }
}

/**
 * A call that failed in a way worth showing the user.
 *
 * [detail] is the API's own sentence when there was one; docs/api.md promises
 * it is human-readable and displayable, so it is shown rather than a status
 * code.
 */
class FcApiException(
    val status: Int,
    val code: String,
    val detail: String,
) : IOException(detail.ifBlank { "$code ($status)" })

/**
 * REST client for the fclights control API.
 *
 * Every mutating call returns the new state, and the same state also arrives
 * on the WebSocket - including for commands this client sent. The reply is
 * still applied, because it is the fastest confirmation available; the socket
 * message that follows is identical and idempotent under the revision guard.
 */
class FcClient(
    private val endpoint: Endpoint,
    private val http: OkHttpClient,
) {
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    suspend fun health(): Health = get("/api/health", Health.serializer())

    suspend fun state(): LightState = StateEnvelopeReader.read(execute(request("GET", "/api/state", null)))

    suspend fun effects(): List<EffectSpec> =
        get("/api/effects", ListSerializer(EffectSpec.serializer()), unwrap = "effects")

    suspend fun layout(): Layout = get("/api/layout", Layout.serializer())

    suspend fun status(): Status = get("/api/status", Status.serializer())

    suspend fun setPower(on: Boolean): LightState =
        send("PUT", "/api/power", encode(PowerRequest.serializer(), PowerRequest(on)))

    suspend fun setBrightness(brightness: Double): LightState =
        send("PUT", "/api/brightness", encode(BrightnessRequest.serializer(), BrightnessRequest(brightness)))

    suspend fun setEffect(effect: String, params: Map<String, JsonElement>? = null): LightState =
        send("PUT", "/api/effect", encode(EffectRequest.serializer(), EffectRequest(effect, params)))

    /** The call to make while a slider is being dragged. */
    suspend fun patchParams(params: Map<String, JsonElement>): LightState =
        send("PATCH", "/api/effect/params", encode(ParamsRequest.serializer(), ParamsRequest(params)))

    suspend fun createScene(name: String): LightState =
        send("POST", "/api/scenes", encode(SceneCreateRequest.serializer(), SceneCreateRequest(name)))

    suspend fun renameScene(id: String, name: String): Scene {
        val body = encode(SceneUpdateRequest.serializer(), SceneUpdateRequest(name = name))
        val text = execute(request("PUT", "/api/scenes/${encodeSegment(id)}", body))
        val root = FcJson.parseToJsonElement(text) as JsonObject
        return FcJson.decodeFromJsonElement(Scene.serializer(), root.getValue("scene"))
    }

    suspend fun captureScene(id: String): LightState {
        val body = encode(SceneUpdateRequest.serializer(), SceneUpdateRequest(capture = true))
        return send("PUT", "/api/scenes/${encodeSegment(id)}", body)
    }

    suspend fun deleteScene(id: String): LightState =
        send("DELETE", "/api/scenes/${encodeSegment(id)}", null)

    suspend fun recallScene(id: String): LightState =
        send("POST", "/api/scenes/${encodeSegment(id)}/recall", EMPTY_BODY)

    // -- plumbing -----------------------------------------------------------

    private fun <T> encode(serializer: kotlinx.serialization.SerializationStrategy<T>, value: T): RequestBody =
        FcJson.encodeToString(serializer, value).toRequestBody(jsonMedia)

    private fun request(method: String, path: String, body: RequestBody?): Request =
        Request.Builder().url(endpoint.baseUrl + path).method(method, body).build()

    private suspend fun <T> get(
        path: String,
        serializer: kotlinx.serialization.DeserializationStrategy<T>,
        unwrap: String? = null,
    ): T {
        val text = execute(request("GET", path, null))
        val element = FcJson.parseToJsonElement(text)
        val target = if (unwrap == null) element else (element as JsonObject).getValue(unwrap)
        return FcJson.decodeFromJsonElement(serializer, target)
    }

    /**
     * Send a mutating command. Every one of them answers with the state
     * envelope, or with a body that carries `state` alongside something else,
     * so both shapes are accepted.
     */
    private suspend fun send(method: String, path: String, body: RequestBody?): LightState =
        StateEnvelopeReader.read(execute(request(method, path, body)))

    private suspend fun execute(request: Request): String =
        suspendCancellableCoroutine { cont ->
            val call = http.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (cont.isActive) cont.resumeWithException(e)
                }

                override fun onResponse(call: Call, response: Response) {
                    val code = response.code
                    val successful = response.isSuccessful
                    // Reading the body can still fail - the headers arriving
                    // does not mean the rest of it will. OkHttp treats a throw
                    // from here as the callback's own problem and will not fall
                    // back to onFailure, so an unhandled one strands the call
                    // suspended for good.
                    val text = runCatching { response.use { it.body?.string().orEmpty() } }
                        .getOrElse { failure ->
                            if (cont.isActive) {
                                cont.resumeWithException(
                                    failure as? IOException ?: IOException(failure)
                                )
                            }
                            return
                        }
                    if (!cont.isActive) return
                    if (successful) {
                        cont.resume(text)
                        return
                    }
                    // The API promises the same error envelope for every
                    // failure, including unmatched routes, and promises the
                    // detail is a displayable sentence.
                    val error = runCatching {
                        FcJson.decodeFromString(ApiError.serializer(), text)
                    }.getOrElse { ApiError("http_$code", text.take(200)) }
                    cont.resumeWithException(FcApiException(code, error.error, error.detail))
                }
            })
        }

    private fun encodeSegment(value: String): String =
        java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")

    private companion object {
        val EMPTY_BODY: RequestBody = ByteArray(0).toRequestBody(null, 0, 0)
    }
}

/**
 * Reads the `{"type": "state", "state": {...}}` envelope the API wraps state
 * in, and also the scene endpoints' `{"scene": ..., "state": ...}`.
 */
internal object StateEnvelopeReader {
    fun read(text: String): LightState {
        val element = FcJson.parseToJsonElement(text)
        val root = element as? JsonObject
            ?: return FcJson.decodeFromJsonElement(LightState.serializer(), element)
        val state = root["state"] ?: return FcJson.decodeFromJsonElement(LightState.serializer(), root)
        return FcJson.decodeFromJsonElement(LightState.serializer(), state)
    }
}
