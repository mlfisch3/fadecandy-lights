package com.fclights.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Wire model for the fclights control API, docs/api.md.
 *
 * Effect parameter values are kept as [JsonElement] rather than being mapped
 * onto Kotlin types, because the app is required to render controls from
 * whatever schema the Pi publishes: an effect added on the Pi has to appear
 * here with no client change, so the app must not know the shape of any
 * particular effect's parameters.
 */
val FcJson: Json = Json {
    ignoreUnknownKeys = true
    encodeDefaults = false
    explicitNulls = false
}

/**
 * A colour value. It remembers *how* it was chosen: a kelvin colour keeps its
 * temperature so the warm-to-cool slider can be put back where the user left
 * it, and `rgb` is always present so a swatch can be drawn without redoing the
 * blackbody conversion.
 */
@Serializable
data class ColorValue(
    val mode: String,
    val kelvin: Double? = null,
    val rgb: List<Int> = listOf(0, 0, 0),
) {
    val isKelvin: Boolean get() = mode == "kelvin" && kelvin != null

    companion object {
        fun ofKelvin(kelvin: Double): ColorValue =
            ColorValue(mode = "kelvin", kelvin = kelvin, rgb = Blackbody.kelvinToRgb255(kelvin))

        fun ofRgb(r: Int, g: Int, b: Int): ColorValue =
            ColorValue(mode = "rgb", rgb = listOf(r.coerceIn(0, 255), g.coerceIn(0, 255), b.coerceIn(0, 255)))
    }
}

@Serializable
data class ParamSpec(
    val name: String,
    val type: String,
    val default: JsonElement,
    val label: String = "",
    val description: String = "",
    val minimum: Double? = null,
    val maximum: Double? = null,
    val step: Double? = null,
    val unit: String = "",
    val choices: List<String>? = null,
    @SerialName("supports_kelvin") val supportsKelvin: Boolean = false,
    @SerialName("kelvin_range") val kelvinRange: List<Double>? = null,
    @SerialName("kelvin_default") val kelvinDefault: Double? = null,
) {
    /** The label the API published, falling back to the wire name. */
    val displayLabel: String get() = label.ifBlank { name }

    val kelvinMin: Double get() = kelvinRange?.getOrNull(0) ?: 1800.0
    val kelvinMax: Double get() = kelvinRange?.getOrNull(1) ?: 6500.0
}

@Serializable
data class EffectSpec(
    val name: String,
    @SerialName("display_name") val displayName: String = "",
    val description: String = "",
    val params: List<ParamSpec> = emptyList(),
) {
    val title: String get() = displayName.ifBlank { name }
}

@Serializable
data class Scene(
    val id: String,
    val name: String,
    val effect: String,
    val params: Map<String, JsonElement> = emptyMap(),
    val brightness: Double = 1.0,
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class LightState(
    val power: Boolean,
    val brightness: Double,
    val effect: String,
    val params: Map<String, JsonElement> = emptyMap(),
    val scenes: List<Scene> = emptyList(),
    @SerialName("active_scene") val activeScene: String? = null,
    val revision: Long = 0,
)

@Serializable
data class Output(
    val index: Int,
    val count: Int,
    val name: String = "",
    val reverse: Boolean = false,
)

@Serializable
data class Device(
    val id: String,
    @SerialName("opc_channel") val opcChannel: Int = 0,
    val serial: String? = null,
    @SerialName("pixel_count") val pixelCount: Int = 0,
    val outputs: List<Output> = emptyList(),
)

@Serializable
data class Bounds(
    val min: List<Double> = emptyList(),
    val max: List<Double> = emptyList(),
)

@Serializable
data class Layout(
    val name: String = "",
    @SerialName("pixel_count") val pixelCount: Int = 0,
    @SerialName("pixels_per_metre") val pixelsPerMetre: Double = 0.0,
    val devices: List<Device> = emptyList(),
    val bounds: Bounds? = null,
)

@Serializable
data class PowerStatus(
    @SerialName("requested_amps") val requestedAmps: Double = 0.0,
    @SerialName("delivered_amps") val deliveredAmps: Double = 0.0,
    @SerialName("limit_amps") val limitAmps: Double = 0.0,
    @SerialName("headroom_amps") val headroomAmps: Double = 0.0,
    val scale: Double = 1.0,
    val clamped: Boolean = false,
)

@Serializable
data class EngineStatus(
    @SerialName("measured_fps") val measuredFps: Double = 0.0,
    @SerialName("frames_dropped") val framesDropped: Long = 0,
    @SerialName("render_ms") val renderMs: Double = 0.0,
    @SerialName("late_frames") val lateFrames: Long = 0,
)

@Serializable
data class Status(
    @SerialName("fps_target") val fpsTarget: Double = 0.0,
    @SerialName("pixel_count") val pixelCount: Int = 0,
    val connected: Boolean = false,
    val dither: Boolean = false,
    val sink: String = "",
    val engine: EngineStatus = EngineStatus(),
    val power: PowerStatus = PowerStatus(),
)

@Serializable
data class Health(
    val ok: Boolean = false,
    val version: String = "",
    val simulated: Boolean = false,
    @SerialName("opc_connected") val opcConnected: Boolean = false,
)

/** The error envelope every failing call returns. */
@Serializable
data class ApiError(
    val error: String = "error",
    val detail: String = "",
)

// -- request bodies ---------------------------------------------------------
//
// The API rejects unknown request fields with 422 rather than ignoring them,
// so these are exact and `encodeDefaults = false` keeps absent fields absent.

@Serializable
data class PowerRequest(val on: Boolean)

@Serializable
data class BrightnessRequest(val brightness: Double)

@Serializable
data class EffectRequest(val effect: String, val params: Map<String, JsonElement>? = null)

@Serializable
data class ParamsRequest(val params: Map<String, JsonElement>)

@Serializable
data class SceneCreateRequest(val name: String)

@Serializable
data class SceneUpdateRequest(val name: String? = null, val capture: Boolean? = null)

// -- WebSocket messages -----------------------------------------------------

/** A decoded message from `ws://<pi>:7891/api/ws`. */
sealed interface WsMessage {
    data class Hello(
        val version: String,
        val state: LightState,
        val layout: Layout?,
        val effects: List<EffectSpec>,
        val status: Status?,
    ) : WsMessage

    data class StateChanged(val state: LightState) : WsMessage

    data class Telemetry(val status: Status) : WsMessage

    data object Pong : WsMessage

    /** Anything the server may add later. Ignored, never fatal. */
    data class Unknown(val type: String) : WsMessage
}

/**
 * Decode one socket frame. Returns null only if the text is not JSON at all;
 * an unrecognised `type` decodes to [WsMessage.Unknown] so a future server can
 * add message types without breaking this client.
 */
fun decodeWsMessage(text: String): WsMessage? {
    val root = runCatching { FcJson.parseToJsonElement(text) as? JsonObject }.getOrNull() ?: return null
    val type = (root["type"] as? JsonPrimitive)?.content ?: return WsMessage.Unknown("")
    return runCatching {
        when (type) {
            "hello" -> WsMessage.Hello(
                version = (root["version"] as? JsonPrimitive)?.content ?: "",
                state = FcJson.decodeFromJsonElement(LightState.serializer(), root.getValue("state")),
                layout = root["layout"]?.let { FcJson.decodeFromJsonElement(Layout.serializer(), it) },
                effects = root["effects"]?.let {
                    FcJson.decodeFromJsonElement(kotlinx.serialization.builtins.ListSerializer(EffectSpec.serializer()), it)
                } ?: emptyList(),
                status = root["status"]?.let { FcJson.decodeFromJsonElement(Status.serializer(), it) },
            )
            "state" -> WsMessage.StateChanged(
                FcJson.decodeFromJsonElement(LightState.serializer(), root.getValue("state"))
            )
            "telemetry" -> WsMessage.Telemetry(
                FcJson.decodeFromJsonElement(Status.serializer(), root.getValue("status"))
            )
            "pong" -> WsMessage.Pong
            else -> WsMessage.Unknown(type)
        }
    }.getOrElse { WsMessage.Unknown(type) }
}

// -- JsonElement conveniences ----------------------------------------------

fun JsonElement.asDoubleOrNull(): Double? = (this as? JsonPrimitive)?.doubleOrNull
fun JsonElement.asIntOrNull(): Int? = (this as? JsonPrimitive)?.intOrNull
fun JsonElement.asBooleanOrNull(): Boolean? = (this as? JsonPrimitive)?.booleanOrNull
fun JsonElement.asStringOrNull(): String? = (this as? JsonPrimitive)?.let { if (it.isString) it.content else null }
fun JsonElement.asColorOrNull(): ColorValue? =
    runCatching { FcJson.decodeFromJsonElement(ColorValue.serializer(), this) }.getOrNull()
