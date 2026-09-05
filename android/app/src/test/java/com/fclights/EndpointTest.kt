package com.fclights

import com.fclights.api.Endpoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The address box is the first-class way in, so it has to forgive whatever the
 * user types: an IP, a hostname, a pasted URL, a port they added themselves.
 */
class EndpointTest {

    @Test
    fun `a bare address takes the default port`() {
        assertEquals(Endpoint("192.168.1.164", 7891), Endpoint.parse("192.168.1.164"))
        assertEquals(Endpoint("fadecandy.local", 7891), Endpoint.parse("fadecandy.local"))
    }

    @Test
    fun `an explicit port is kept`() {
        assertEquals(Endpoint("fadecandy", 8080), Endpoint.parse("fadecandy:8080"))
    }

    @Test
    fun `a pasted URL is accepted`() {
        assertEquals(Endpoint("192.168.1.164", 7891), Endpoint.parse("http://192.168.1.164:7891/api"))
        assertEquals(Endpoint("pi", 7891), Endpoint.parse("ws://pi/api/ws"))
    }

    @Test
    fun `surrounding whitespace is forgiven`() {
        assertEquals(Endpoint("192.168.1.164", 7891), Endpoint.parse("  192.168.1.164  "))
    }

    @Test
    fun `an IPv6 literal is bracketed in the URLs it builds`() {
        val endpoint = Endpoint.parse("fe80::1")!!
        assertEquals("fe80::1", endpoint.host)
        assertEquals("http://[fe80::1]:7891", endpoint.baseUrl)
        assertEquals("ws://[fe80::1]:7891/api/ws", endpoint.wsUrl)
    }

    @Test
    fun `a bracketed IPv6 address may carry a port`() {
        assertEquals(Endpoint("fe80::1", 9000), Endpoint.parse("[fe80::1]:9000"))
    }

    @Test
    fun `nonsense is rejected rather than half-parsed`() {
        assertNull(Endpoint.parse(""))
        assertNull(Endpoint.parse("   "))
        assertNull(Endpoint.parse(":7891"))
        assertNull(Endpoint.parse("pi:not-a-port"))
        assertNull(Endpoint.parse("pi:99999"))
    }

    @Test
    fun `the URLs it builds match the documented paths`() {
        val endpoint = Endpoint("192.168.1.164")
        assertEquals("http://192.168.1.164:7891", endpoint.baseUrl)
        assertEquals("ws://192.168.1.164:7891/api/ws", endpoint.wsUrl)
    }

    @Test
    fun `the default port is left out of what is shown and remembered`() {
        assertEquals("192.168.1.164", Endpoint("192.168.1.164").toString())
        assertEquals("192.168.1.164:9000", Endpoint("192.168.1.164", 9000).toString())
        // Round-trips through the string the app stores in preferences.
        assertEquals(Endpoint("pi", 9000), Endpoint.parse(Endpoint("pi", 9000).toString()))
    }
}
