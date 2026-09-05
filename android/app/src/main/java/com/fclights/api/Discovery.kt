package com.fclights.api

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** A controller found on the network. */
data class Found(
    val name: String,
    val endpoint: Endpoint,
    val pixels: Int? = null,
    val version: String? = null,
)

/**
 * mDNS discovery of `_fclights._tcp`.
 *
 * Treated as a convenience, never as the way in. Plenty of home routers drop
 * multicast, Android's battery saver suppresses it, and the captain's own
 * machine cannot resolve mDNS at all - so the app's manual address box is the
 * first-class path and this is the shortcut. A flow that never emits is a
 * normal outcome here, not an error.
 */
class Discovery(context: Context) {

    private val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as? NsdManager

    fun browse(): Flow<Found> = callbackFlow {
        val manager = nsd
        if (manager == null) {
            close()
            return@callbackFlow
        }

        // Resolves are serialised: NsdManager.resolveService fails with
        // FAILURE_ALREADY_ACTIVE if one is already in flight, which on a
        // network with several services silently loses all but the first.
        val pending = ArrayDeque<NsdServiceInfo>()
        var resolving = false

        fun resolved(info: NsdServiceInfo) {
            val host = hostAddress(info) ?: return
            trySend(
                Found(
                    name = info.serviceName ?: host,
                    endpoint = Endpoint(host, if (info.port > 0) info.port else Endpoint.DEFAULT_PORT),
                    pixels = txt(info, "pixels")?.toIntOrNull(),
                    version = txt(info, "version"),
                )
            )
        }

        fun pump() {
            if (resolving) return
            val next = pending.removeFirstOrNull() ?: return
            resolving = true
            @Suppress("DEPRECATION")
            manager.resolveService(next, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                    resolving = false
                    pump()
                }

                override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                    resolving = false
                    resolved(serviceInfo)
                    pump()
                }
            })
        }

        val listener = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                close()
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
            override fun onDiscoveryStarted(serviceType: String) = Unit
            override fun onDiscoveryStopped(serviceType: String) = Unit

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                pending.addLast(serviceInfo)
                pump()
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) = Unit
        }

        manager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        awaitClose {
            runCatching { manager.stopServiceDiscovery(listener) }
        }
    }

    private fun txt(info: NsdServiceInfo, key: String): String? =
        info.attributes[key]?.toString(Charsets.UTF_8)

    /**
     * A resolved service's address. `hostAddresses` replaced the single `host`
     * in API 34; the older accessor is still the only one on the phones this
     * app supports down to.
     */
    private fun hostAddress(info: NsdServiceInfo): String? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            info.hostAddresses.firstOrNull()?.hostAddress
        } else {
            @Suppress("DEPRECATION")
            info.host?.hostAddress
        }

    private companion object {
        const val SERVICE_TYPE = "_fclights._tcp."
    }
}
