package com.fclights.model

/**
 * Everything the app knows about the controller, and the pure reduction that
 * keeps it in step.
 *
 * Kept free of Android and of OkHttp on purpose: the ordering rule that lets
 * two phones agree is small enough to state, and small enough to test, only if
 * it lives somewhere a unit test can reach.
 */
data class ControllerState(
    val state: LightState? = null,
    val effects: List<EffectSpec> = emptyList(),
    val layout: Layout? = null,
    val status: Status? = null,
    val version: String = "",
) {
    /** The schema of the effect currently running, if the server has published one. */
    val activeEffect: EffectSpec?
        get() = state?.let { s -> effects.firstOrNull { it.name == s.effect } }

    fun effect(name: String): EffectSpec? = effects.firstOrNull { it.name == name }
}

/**
 * Apply one socket message.
 *
 * `revision` increases on every state change, and a state carrying a revision
 * lower than one already applied is discarded. docs/api.md calls that the only
 * ordering guarantee needed to keep several phones in step, and it is what
 * stops a slow REST reply from undoing a newer socket push.
 *
 * A `hello` is adopted wholesale, including its state, whatever its revision:
 * it is a full resync after a reconnect, and the controller restores its last
 * state on boot, so what it says is the truth even if it counted down.
 */
fun reduce(current: ControllerState, message: WsMessage): ControllerState = when (message) {
    is WsMessage.Hello -> ControllerState(
        state = message.state,
        effects = message.effects.ifEmpty { current.effects },
        layout = message.layout ?: current.layout,
        status = message.status ?: current.status,
        version = message.version,
    )

    is WsMessage.StateChanged -> applyState(current, message.state)

    is WsMessage.Telemetry -> current.copy(status = message.status)

    WsMessage.Pong -> current
    is WsMessage.Unknown -> current
}

/**
 * Adopt a state unless it is older than what is already showing.
 *
 * Used for socket pushes and for the state every mutating REST call returns;
 * the two race, and the revision decides.
 */
fun applyState(current: ControllerState, incoming: LightState): ControllerState {
    val existing = current.state
    if (existing != null && incoming.revision < existing.revision) return current
    return current.copy(state = incoming)
}
