package com.fclights.api

import android.content.Context
import androidx.core.content.edit

/** Remembers the last address that worked, so the app opens straight onto the lights. */
class Prefs(context: Context) {

    private val store = context.applicationContext.getSharedPreferences("fclights", Context.MODE_PRIVATE)

    var endpoint: Endpoint?
        get() = store.getString(KEY_ENDPOINT, null)?.let { Endpoint.parse(it) }
        set(value) {
            store.edit {
                if (value == null) remove(KEY_ENDPOINT) else putString(KEY_ENDPOINT, value.toString())
            }
        }

    private companion object {
        const val KEY_ENDPOINT = "endpoint"
    }
}
