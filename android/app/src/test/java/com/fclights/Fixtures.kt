package com.fclights

/**
 * Verbatim responses captured from the real controller, loaded by name.
 *
 * The app is written against a published contract it does not own, so the
 * tests parse what the service actually emits rather than what this repo
 * believes it emits.
 */
object Fixtures {
    fun read(name: String): String =
        requireNotNull(javaClass.classLoader?.getResourceAsStream(name)) { "missing fixture $name" }
            .use { it.readBytes().toString(Charsets.UTF_8) }

    val hello: String get() = read("fixture_hello.json")
    val state: String get() = read("fixture_state.json")
    val effects: String get() = read("fixture_effects.json")
    val layout: String get() = read("fixture_layout.json")
    val status: String get() = read("fixture_status.json")
    val sceneCreate: String get() = read("fixture_scene_create.json")
}
