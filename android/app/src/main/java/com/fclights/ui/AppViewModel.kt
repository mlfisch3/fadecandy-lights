package com.fclights.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.fclights.api.Discovery
import com.fclights.api.Endpoint
import com.fclights.api.FcApiException
import com.fclights.api.FcClient
import com.fclights.api.FcSocket
import com.fclights.api.Found
import com.fclights.api.Link
import com.fclights.api.Prefs
import com.fclights.model.ColorValue
import com.fclights.model.ControllerState
import com.fclights.model.EffectSpec
import com.fclights.model.LightState
import com.fclights.model.Params
import com.fclights.model.applyState
import com.fclights.model.reduce
import com.fclights.model.retirePending
import com.fclights.model.retirePendingBrightness
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import okhttp3.OkHttpClient
import java.io.IOException
import java.util.concurrent.TimeUnit

/** How the app describes its link to the controller in the UI. */
enum class Connection { Disconnected, Connecting, Connected }

data class UiState(
    val endpoint: Endpoint? = null,
    val connection: Connection = Connection.Disconnected,
    val connectionDetail: String = "",
    val controller: ControllerState = ControllerState(),
    val discovered: List<Found> = emptyList(),
    val discovering: Boolean = false,
    val error: String? = null,
    /**
     * Values the user is currently dragging. While a key is here the UI shows
     * this instead of the controller's value, so a state push that is a few
     * hundred milliseconds behind the finger does not yank the slider back.
     */
    val pendingParams: Map<String, JsonElement> = emptyMap(),
    val pendingBrightness: Double? = null,
) {
    val state: LightState? get() = controller.state
    val effects: List<EffectSpec> get() = controller.effects
    val activeEffect: EffectSpec? get() = controller.activeEffect

    /** The parameter values to render: the controller's, overlaid with any live drag. */
    val paramValues: Map<String, JsonElement>
        get() = (state?.params ?: emptyMap()) + pendingParams

    val brightness: Double get() = pendingBrightness ?: state?.brightness ?: 0.0
}

/**
 * Holds the connection and turns user gestures into API calls.
 *
 * Two rules from docs/api.md shape this class. Commands go over REST while
 * state arrives on the socket, and the socket's messages are applied even when
 * they were caused by this app's own command - that echo is what keeps two
 * phones in agreement. And a drag is throttled to about 10 Hz with a final
 * value on release, because the engine renders at 60 fps regardless and there
 * is nothing to gain from matching it.
 */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        // Keeps an idle socket alive through a home router's NAT timeout.
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private val prefs = Prefs(app)
    private val discovery = Discovery(app)
    private val socket = FcSocket(http)

    private val _ui = MutableStateFlow(UiState(endpoint = prefs.endpoint))
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    private var client: FcClient? = null
    private var socketJob: Job? = null
    private var discoveryJob: Job? = null

    private val paramSends =
        ThrottledSender(viewModelScope, THROTTLE_MILLIS) { final -> sendParams(final) }
    private val brightnessSends =
        ThrottledSender(viewModelScope, THROTTLE_MILLIS) { final -> sendBrightness(final) }

    init {
        _ui.value.endpoint?.let { connect(it) }
        startDiscovery()
    }

    // -- connection ---------------------------------------------------------

    fun connect(endpoint: Endpoint) {
        if (!endpoint.isUsable) {
            // Remembering it is what would turn a typo into an app that cannot
            // be started rather than a connection that failed.
            _ui.value = _ui.value.copy(error = "Not an address this app can use: $endpoint")
            return
        }
        socketJob?.cancel()
        // An address has been chosen; there is nothing left to look for, and a
        // browse costs multicast traffic for as long as it runs.
        stopDiscovery()
        client = FcClient(endpoint, http)
        prefs.endpoint = endpoint
        _ui.value = _ui.value.copy(
            endpoint = endpoint,
            connection = Connection.Connecting,
            connectionDetail = "",
            controller = ControllerState(),
            error = null,
        )
        openSocket(endpoint)
    }

    /**
     * Start following the live state, if nothing is following it already.
     *
     * Tied to whether the app is on screen: a socket nobody is looking at costs
     * traffic and a watchdog timer for a screen that is not being drawn, and
     * coming back to the app should land on live state rather than on whatever
     * was last seen before it went away.
     */
    fun onVisible() {
        if (socketJob?.isActive == true) return
        _ui.value.endpoint?.let { openSocket(it) }
    }

    fun onHidden() {
        socketJob?.cancel()
        socketJob = null
    }

    private fun openSocket(endpoint: Endpoint) {
        socketJob?.cancel()
        socketJob = viewModelScope.launch {
            try {
                follow(endpoint)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Nothing above this has a handler: an escape here would reach
                // the thread's uncaught handler and take the process with it.
                _ui.value = _ui.value.copy(connection = Connection.Disconnected)
                report(e)
            }
        }
    }

    private suspend fun follow(endpoint: Endpoint) {
        socket.connect(endpoint).collect { link ->
            when (link) {
                is Link.Connecting -> _ui.value = _ui.value.copy(connection = Connection.Connecting)
                is Link.Down -> _ui.value = _ui.value.copy(
                    connection = Connection.Disconnected,
                    connectionDetail = link.reason,
                )
                is Link.Up -> _ui.value = _ui.value.copy(
                    connection = Connection.Connected,
                    connectionDetail = "",
                    controller = reduce(_ui.value.controller, link.message),
                )
            }
        }
    }

    /**
     * Run one scan. [Discovery.browse] is bounded, so this completes on its own
     * and finding nothing simply leaves the sheet offering the address box.
     */
    fun startDiscovery() {
        if (discoveryJob?.isActive == true) return
        _ui.value = _ui.value.copy(discovering = true, discovered = emptyList())
        discoveryJob = viewModelScope.launch {
            try {
                discovery.browse().collect { found ->
                    val current = _ui.value.discovered
                    if (current.none { it.endpoint == found.endpoint }) {
                        _ui.value = _ui.value.copy(discovered = current + found)
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // Discovery failing is ordinary; the address box is the way in.
            } finally {
                _ui.value = _ui.value.copy(discovering = false)
            }
        }
    }

    fun stopDiscovery() {
        discoveryJob?.cancel()
        discoveryJob = null
        _ui.value = _ui.value.copy(discovering = false)
    }

    fun dismissError() {
        _ui.value = _ui.value.copy(error = null)
    }

    // -- commands -----------------------------------------------------------

    fun setPower(on: Boolean) = command { it.setPower(on) }

    fun setBrightness(value: Double) {
        _ui.value = _ui.value.copy(pendingBrightness = value.coerceIn(0.0, 1.0))
        brightnessSends.request(final = false)
    }

    /**
     * The finger has lifted. No value is passed back down: what a control drew
     * last is not necessarily what it last sent, because a drag frame and the
     * release can arrive in the same input batch with no composition between
     * them. The value already pending here is the one the finger stopped on.
     */
    fun commitBrightness() = brightnessSends.request(final = true)

    fun selectEffect(name: String) = command {
        // Params omitted: the controller fills in the effect's declared
        // defaults, which is what selecting an effect should mean.
        it.setEffect(name)
    }

    fun setParam(name: String, value: JsonElement) {
        _ui.value = _ui.value.copy(pendingParams = _ui.value.pendingParams + (name to value))
        paramSends.request(final = false)
    }

    /** As [commitBrightness], for whichever parameters a control has left pending. */
    fun commitParam() = paramSends.request(final = true)

    fun setColorParam(name: String, value: ColorValue) =
        setParam(name, Params.encodeColor(value))

    fun saveScene(name: String) = command { it.createScene(name) }

    fun recallScene(id: String) = command { it.recallScene(id) }

    fun deleteScene(id: String) = command { it.deleteScene(id) }

    fun captureScene(id: String) = command { it.captureScene(id) }

    // -- sending ------------------------------------------------------------

    private suspend fun sendParams(final: Boolean) {
        val api = client ?: return
        val pending = _ui.value.pendingParams
        if (pending.isEmpty()) return
        try {
            val state = api.patchParams(pending)
            adopt(state)
            // Only drop the override once the controller has confirmed the
            // value; dropping it earlier makes the control jump back to a
            // state message that is still in flight.
            if (final) retire(pending)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (final) retire(pending)
            report(e)
        }
    }

    private fun retire(sent: Map<String, JsonElement>) {
        _ui.value = _ui.value.copy(pendingParams = retirePending(_ui.value.pendingParams, sent))
    }

    private suspend fun sendBrightness(final: Boolean) {
        val api = client ?: return
        val value = _ui.value.pendingBrightness ?: return
        try {
            adopt(api.setBrightness(value))
            if (final) retireBrightness(value)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (final) retireBrightness(value)
            report(e)
        }
    }

    private fun retireBrightness(sent: Double) {
        _ui.value = _ui.value.copy(
            pendingBrightness = retirePendingBrightness(_ui.value.pendingBrightness, sent)
        )
    }

    // -- plumbing -----------------------------------------------------------

    private fun command(block: suspend (FcClient) -> LightState) {
        val api = client ?: return
        viewModelScope.launch {
            try {
                adopt(block(api))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                report(e)
            }
        }
    }

    /**
     * Apply a state that came back from a command. The revision guard settles
     * the race against the socket push carrying the same change.
     */
    private fun adopt(state: LightState) {
        _ui.value = _ui.value.copy(controller = applyState(_ui.value.controller, state))
    }

    private fun report(e: Exception) {
        val message = when (e) {
            is FcApiException -> e.detail.ifBlank { "${e.code} (${e.status})" }
            is IOException -> "Cannot reach the controller"
            else -> e.message ?: e.javaClass.simpleName
        }
        _ui.value = _ui.value.copy(error = message)
    }

    override fun onCleared() {
        socketJob?.cancel()
        discoveryJob?.cancel()
        http.dispatcher.executorService.shutdown()
        super.onCleared()
    }

    private companion object {
        const val THROTTLE_MILLIS = 100L
    }
}
