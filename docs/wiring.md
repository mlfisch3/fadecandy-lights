# Wiring

This document is meant to be pulled cable from.
It covers what the Fadecandy's output header actually is, how to get 5 V to the strips without wiring the whole apartment back to one box, how to size conductors and fuses, and what to check with a meter before anything is energised.

Everything electrical here is traced to a primary source, and the sources are listed at the end.
Where something could not be established, it is marked **unverified** rather than smoothed over.

Wire colours throughout the diagrams match the pigtails on your strips: **black = GND, yellow = DATA, red = +5 V**.
Every conductor is also labelled in text, so the diagrams still work printed in grey.

> ## Read this if you read an earlier version of this document
>
> **§6.3 used to say a 64-pixel run does not need injection at its far end. That was wrong, and this revision reverses it.**
>
> The old thresholds were worked from 5.00 V arriving at the strip, when the distribution wiring spends 0.25 V getting there. Every one of them was therefore twice as generous as it should have been. Corrected, feeding a 2.11 m run from one end needs a strip rail resistance no flexible PCB achieves.
>
> **At full white, every 64-pixel run is fed at both ends: two +5 V leads and two GND leads per run, both off that run's own branch fuse.** It is the design, not a contingency for bad strips.
>
> Your far ends are unterminated inside sealed sleeves, so that means opening and resealing about 18 of them. §6.3 now states that work up front and gives you the one lever that avoids it: fed from the DI end alone, a 25 % brightness cap asks exactly the same of the strip that full white fed from both ends does. Neither choice changes a gauge or a fuse rating.
>
> Two other numbers moved enough to matter if you were about to buy from the old text. Branch wire is **14 AWG to 1.87 m per leg**, not 18 or 16 AWG, and not a round 2 m. The branch fuse is **6 A**, not 5 A. §6.2 is the authoritative table and every figure in it is derived there from a cited standard.

---

## 1. The hardware, as measured

| | |
|---|---|
| Strips | WS2812B in clear silicone sleeving, 33 mm centre to centre, so exactly 30 LEDs/m |
| Run length | 64 LEDs, 2.11 m |
| Topology | Data lands on DI at one end only; the far end is unterminated. Each run is independent, not chained. |
| Pigtail | Black = GND, yellow = DATA, red = +5 V, all at the DI end |
| Quantity | About 18 runs |
| Supplies | LETOUR S-150-5 (5 V, 30 A) and sompom S-300-5 (5 V, 60 A) |
| Controllers | Several Fadecandy boards |
| Host | Raspberry Pi 3 Model B+ |

Because each run is independent rather than chained, 18 runs need 18 data channels, which is why the board count matters.
One Fadecandy gives 8.

---

## 2. What the Fadecandy output header actually is

This section is the one worth reading twice, because the rest depends on it.
It was verified three ways that agree with each other: the Eagle schematic netlist, the Eagle board file's silkscreen and pad geometry, and the firmware's pin assignments.

### 2.1 The header is GND and DATA only. There is no +5 V pin on it.

J2 is a 2 × 8 header labelled `Outputs` in the schematic.
In the netlist, the odd pins (1, 3, 5, 7, 9, 11, 13, 15) are all tied to `GND`.
Each even pin (2, 4, … 16) connects through one 68 Ω series resistor to one output of the buffer.
No pin on J2 touches `+5V`, `+3V3`, or the buffer's supply rail.

In the board file, the silkscreen prints `0` through `7` above the header and a `+` / `−` pair for each channel.
The `−` row is the one nearest the board edge.
The designer's own build guide says the same thing in plain words: *"The GND wire needs to connect to the − terminal on the Fadecandy, and the DIN wire connects to +. There are tiny + and − labels on the board, or you can remember that the pins nearest to the edge are all −."*

**So: strip power cannot come from the Fadecandy.
It must come from the external supply.**
The same guide is explicit that *"The LEDs themselves draw so much power that they need a separate power brick, but the controller board requires very little power."*

The only 5 V rail on the board is the AAT3110-5.0 charge pump that supplies the level shifter.
That part is rated for about 100 mA total, and it is already spoken for.
It is not a power source for anything external.

### 2.2 Channel number to pin, verified end to end

The firmware writes all eight outputs as one byte of PORTD.
`OctoWS2811z.cpp` assigns strip 1 to pin 2, strip 2 to pin 14, strip 3 to pin 7, strip 4 to pin 8, strip 5 to pin 6, strip 6 to pin 20, strip 7 to pin 21, strip 8 to pin 5.
`core_pins.h` maps those to PORTD bits 0, 1, 2, 3, 4, 5, 6, 7 in that order.
The schematic then runs PTD0 through the buffer and its 68 Ω resistor to J2 pin 16, PTD1 to pin 14, and so on down to PTD7 at pin 2.
The board file puts J2 pin 16 at the same x coordinate as the silkscreen `0`, and pin 2 at the silkscreen `7`.

| fcserver channel | silkscreen | firmware "strip" | MCU pin | J2 DATA pin (`+`) | J2 GND pin (`−`) |
|---|---|---|---|---|---|
| 0 | `0` | strip 1 | PTD0 | 16 | 15 |
| 1 | `1` | strip 2 | PTD1 | 14 | 13 |
| 2 | `2` | strip 3 | PTD2 | 12 | 11 |
| 3 | `3` | strip 4 | PTD3 | 10 | 9 |
| 4 | `4` | strip 5 | PTD4 | 8 | 7 |
| 5 | `5` | strip 6 | PTD5 | 6 | 5 |
| 6 | `6` | strip 7 | PTD6 | 4 | 3 |
| 7 | `7` | strip 8 | PTD7 | 2 | 1 |

In practice you do not need the J2 pin numbers.
Read the silkscreen digit, take `+` for data and `−` for ground, and remember the edge row is ground.

### 2.3 The outputs are 5 V, not 3.3 V

The data outputs are level shifted.
The schematic shows an `SN74HCT245PWR` octal buffer whose `VCC` net is the output of an `AAT3110IGU-5.0-T1` regulated charge pump, with `OE` tied to ground and `DIR` tied to that same 5 V rail, so all eight channels are permanently enabled in the A-to-B direction.
Each output then passes through a 68 Ω series resistor before reaching the header.

The upstream `pcb/README.md` states the intent directly: *"The Teensy uses 3.3v IO which doesn't work reliably with the WS2811 LEDs.
Fadecandy includes a level shifter and series termination resistors.
Fadecandy also includes a boost converter power supply to generate a stable 5V for the level shifter even when the USB power is noisy or sagging due to long cables."*

This is a real, verified answer, not an assumption: **the edge that leaves a Fadecandy output is a 5 V edge.**

### 2.4 The logic margin, and why you must not turn the supply above 5.0 V

The WS2812B datasheet gives `VIH` for DIN as **0.7 × VDD** minimum, and `VIL` as 0.3 × VDD maximum, over a characterised supply range of 4.5 V to 5.5 V.
Note what that means: the threshold the Fadecandy must clear is a fraction of *the strip's own supply*, not a fixed voltage.

The TI datasheet for the SN74HCT245 gives, at `VCC` = 4.5 V:

| Condition | `VOH` min |
|---|---|
| `IOH` = −20 µA (light load, which is this case) | 4.40 V |
| `IOH` = −6 mA, over temperature | 3.84 V |

A WS2812B data input draws ±1 µA and presents 15 pF, so the steady-state load is the light-load column; the −6 mA figure is the pessimistic bound during edges into cable capacitance.
The Fadecandy's buffer rail is a regulated 5.0 V charge pump rather than 4.5 V, so the real numbers are better than the table.

Working the margin at the pessimistic end:

```
strip at 5.0 V   VIH = 3.50 V   vs 3.84 V worst-case VOH   margin +0.34 V   OK
strip at 5.5 V   VIH = 3.85 V   vs 3.84 V worst-case VOH   margin  −0.01 V  NOT OK
```

This class of supply normally has an output trim adjustment, usually marked V-ADJ.
The tempting move is to wind it up to "make up for voltage drop in the cable."
Do not.
Raising the strip's supply raises the bar the Fadecandy has to clear, and it eats the margin you were trying to protect.

**Set the supplies to 5.00 V, measured at the supply terminals with a meter, and fix voltage drop with copper instead.**

### 2.5 Ground must be common, and data should be paired with its own ground

The data signal is a voltage measured against ground.
The Fadecandy drives it against its ground; each WS2812B reads it against its own supply's ground.
If those grounds are not bonded, the LEDs have no reference and the run stays dark, flickers, or shows garbage.

The designer's recommended practice is stronger than "bond it somewhere": *"The recommended way of wiring the Fadecandy Controller's ground (GND) wire is to run separate wires from the Fadecandy Controller to your LED strips, and to keep its ground wire paired with its data wire.
Keeping your data wires and power wires separate is good practice for creating reliable projects.
At this small of a scale it isn't a big deal, but this will help a lot with reliability on larger projects."*

That is why J2 gives you eight ground pins rather than one.
Use the ground pin next to the channel you are using, run it alongside that channel's data wire, and land the pair at the run.

---

## 3. Diagram 1 - System overview

![System overview: Pi to Fadecandy over USB, eight DATA + GND pairs out to eight runs, a 5 V supply feeding +5 V and GND bus bars through a 40 A main fuse, a 6 A branch fuse per run, and the common ground tie](diagrams/01-system-overview.svg)

The thing to take from this drawing is the current path.
Strip current leaves the supply's V+, goes through the LEDs, and returns to the supply's V−.
It never passes through the Fadecandy or the Pi.
The Fadecandy contributes a data edge and a ground reference and nothing else; it is powered from USB and draws on the order of tens of milliamps.

The Pi has its own 5 V supply.
Do not try to run the Pi from the LED supply, and do not try to run LEDs from the Pi.

---

## 4. Diagram 2 - One channel in detail

![One Fadecandy channel wired to one 64-pixel run, showing the DI pigtail, the supply connections and the point where ground is made common](diagrams/02-one-channel.svg)

Per channel you make six connections: two at the board, and four at the run - two at each of its two ends.

At the board, and at the run's DI end:

1. Fadecandy channel *n* `+` pad → the run's yellow DATA lead.
2. Fadecandy channel *n* `−` pad → the run's black GND lead.
    Twist this with the data wire; the pair travels together the whole way.

At the run's DI end, where the pigtail already is:

3. The run's black GND lead → the GND bus.
4. The run's red +5 V lead → the +5 V bus, through that run's 6 A branch fuse.

At the run's far end, where you have to make the joint yourself:

5. A second black GND leg, soldered to the strip's GND rail → the same GND bus.
6. A second red +5 V leg, soldered to the strip's +5 V rail → the output of that **same** 6 A branch fuse, not a second fuse.

Connections 5 and 6 are Option A in §6.3, which is what this document sizes and what the diagrams draw.
Under Option B there you feed the DI end only, at a stated brightness cap, and connections 5 and 6 do not exist.
Read §6.3 before you cut the far end of a sleeve open, because that is the decision it settles.

Connections 2 and 3 meet at a junction, and that junction is where ground becomes common.
Physically the neatest place for it is the ground terminal of the fused distribution block that serves that run, so the run's ground, the Fadecandy's ground wire, and the supply's V− all land on the same bar.

The diagram also shows what sits behind each `+` pad, since it explains the numbers in §2.4: MK20DX128 PTD*n* at 3.3 V, into the SN74HCT245 buffer at 5.0 V, through a 68 Ω series resistor, out to the pad.

---

## 5. Diagram 3 - Topology: one central point, or clusters?

![Three topologies compared: all runs home to one point, clustering with local supplies, and clustering with a Fadecandy per zone](diagrams/03-topology.svg)

This is the direct answer to "there will be a lot of wires running around."

### 5.1 Why all-home-to-one-point fails

Voltage drop is set by the round trip, so count the conductor out and back:

```
R_loop = 2 × length_m × ρ          V_drop = I × R_loop
```

This is the conductor table the whole document is built on.
It is the single input to every gauge, every fuse rating and every length in §6.2, so it names its sources rather than describing itself as typical:

| AWG | loop Ω/m, out and back | ampacity | source of the ampacity |
|---|---|---|---|
| 16 | 0.0333 | 8 A | NEC Table 402.5 |
| 14 | 0.0209 | 15 A | NEC Table 310.16, 60 °C column |
| 12 | 0.0132 | 20 A | NEC Table 310.16, 60 °C column |
| 10 | 0.0083 | 30 A | NEC Table 310.16, 60 °C column |
| 8 | 0.0052 | 40 A | NEC Table 310.16, 60 °C column |
| 6 | 0.0032 | 55 A | NEC Table 310.16, 60 °C column |
| 4 | 0.0020 | 70 A | NEC Table 310.16, 60 °C column |
| 2 | 0.0013 | 95 A | NEC Table 310.16, 60 °C column |

**Resistance** is NEC Chapter 9, Table 8, uncoated *stranded* copper, DC, at 75 °C, doubled to give the round trip.
Three deliberate choices are baked into that, all in the conservative direction.
Stranded rather than solid, because hand-terminated distribution wiring is stranded, and stranded is about 4 % more resistive than solid of the same AWG.
Seventy-five degrees rather than twenty, because that is the basis NEC voltage-drop work uses; a conductor actually running at 30 °C is about 15 % less resistive, so every length limit in this document carries that much margin.
Loop rather than one-way, because the current goes out and comes back and both conductors drop.

**Ampacity** is NEC Table 310.16, 60 °C copper column.
The 60 °C column is the right one here even if you buy 105 °C wire: NEC 110.14(C)(1)(a) sizes conductors on circuits of 100 A or less to the 60 °C column unless the *terminals* are listed for more, and the screw terminals on an open-frame supply and a distribution block are not.
Sizing to the 60 °C column also satisfies NEC 240.4(D)'s small-conductor overcurrent limits automatically, so there is no second rule to remember.

Sixteen AWG is below the range Article 310 covers, so its 8 A comes from NEC Table 402.5.
Be careful with 16 AWG: the figure widely quoted for it is 10 A, and 10 A is not its ampacity - it is NEC 240.4(D)(2)'s *maximum overcurrent protection*, which is a different quantity and a larger one.
Nothing smaller than 16 AWG carries power anywhere in this system.

One run at full white draws 3.84 A (see §7).
Of the 0.50 V of total drop §6.2 has to spend between the supply terminals and the dimmest LED, the branch wire from the distribution block to the run gets **0.150 V**.
Take a single conductor at a time against that allowance and the shape of the problem is immediate:

| Branch wire alone | R_loop | Drop at 3.84 A | Against the 0.150 V allowance |
|---|---|---|---|
| 6.0 m, 18 AWG | 0.318 Ω | 1.22 V | 8× over |
| 6.0 m, 12 AWG | 0.079 Ω | 0.30 V | 2× over |
| 2.0 m, 16 AWG | 0.067 Ω | 0.26 V | 1.7× over |
| 2.0 m, 14 AWG | 0.042 Ω | 0.16 V | just over; 1.87 m is 14 AWG's limit |
| 1.5 m, 14 AWG | 0.031 Ω | 0.12 V | fits, with room to spare |

Read the first row as the all-home-to-one-point case.
At 1.22 V of drop the run sits at 3.8 V, well below the 4.5 V the WS2812B is characterised at; it goes dim and shifts pink, and because `VIH` is 0.7 × its own supply, the logic threshold sags with it.
Making that row work by brute force means 12 AWG to all 18 runs and it *still* does not fit - 6 m of 12 AWG is twice the branch allowance on its own, before the feeder and the bus have spent anything.
There is no gauge you can buy that makes a 6 m branch work at 5 V, which is the entire argument of this section.

The lesson is in the last two rows.
The difference between fitting and not fitting is 0.5 m of length, not a gauge.
Distance is the expensive variable at 5 V; copper is the one you cannot buy your way out with.

### 5.2 Why long data runs are cheap, and where they stop being cheap

A WS2812B data input draws ±1 µA and presents 15 pF.
Gauge is essentially irrelevant; 24 to 26 AWG is ample at any length you would use indoors.
The constraint is timing, not current.

```
bit period    1.25 µs      T0H 0.4 µs     T1H 0.8 µs
tolerance     ±150 ns      propagation    ~5 ns per metre
```

A reflection on an unterminated line makes a round trip, so 10 m is roughly 100 ns - the same order as the entire ±150 ns budget.
The Fadecandy's 68 Ω series resistor is source termination and damps that reflection, but source termination only works when the line impedance is somewhere near the source impedance.
That means running DATA twisted with its own GND, which gives you a defined pair, rather than running a lone wire whose impedance is whatever the room happens to make it.

Practical limits.
These are engineering practice, not a datasheet number, and are marked as such:

| Distance | What to do |
|---|---|
| up to ~2 m | plain hookup wire, data and its ground kept together |
| up to ~5 m | twisted pair, routed away from mains cable. Do this by default. |
| beyond ~5 m | do not stretch it. Either move the Fadecandy into the zone and extend USB instead, or convert to a differential RS-485 pair and convert back at the run. |

### 5.3 Recommended layout

**Supplies and fused distribution local to each cluster.
Only data and its ground travel any distance.**

Group the runs by room.
Give each group its own fused distribution block, sited so that no run's branch leg exceeds **1.8 m**, which is what 14 AWG reaches on its 0.150 V allowance (§6.2).
Keep the supply itself **within about 0.5 m of the block**.
That is not a rule of thumb: at eight runs the feeder carries 30.7 A, and §6.2's table gives 8 AWG 0.47 m and even 6 AWG only 0.76 m before the feeder's own drop eats its share of the budget.
Two groups may share one supply only if they are adjacent enough for it to sit within that distance of both blocks; otherwise each group gets its own supply.

Then pick how the data gets there:

- If the Fadecandy can sit within about 5 m of every run in the cluster, one central Fadecandy is simplest. You run one twisted DATA + GND pair per run.
- If it cannot, put a Fadecandy in the cluster too and run USB to it. Then exactly one cable crosses the room per zone, which is the smallest number of long wires this system can be built with.

**One question overrides that distance one: would a single central board end up driving runs fed from two different supplies?**
If it would, do not use a central board however short the data runs are.
Put a Fadecandy in each zone instead, so no board's 24-26 AWG ground wires become the bridge between two power zones - the arrangement §9.2 rules out for the same reason.
A board may be central only while every run it drives comes off one supply.

Note that if two clusters run from two different supplies and both clusters' grounds return to Fadecandy boards, those supply grounds end up connected.
That is correct and necessary.
What must never happen is paralleling the two supplies' **V+** outputs.
Tie grounds; never tie positives.

**Wherever you use more than one supply, bond every supply's V− terminal to every other**, with a conductor sized like the largest zone's feeder - 8 AWG in the §9.2 layout.
This is a requirement, not a refinement, and the reason is worth stating precisely, because it is not the obvious one.

The boards and the USB hub **already** tie those supply grounds together, whether you want them to or not.
Every board's `−` pins land on its zone's GND bus, and every board shares a ground with every other board through the hub.
Those ties are 24-26 AWG signal returns: they are the reference for the data line and nothing more, and **they must never be the only tie between two supplies.**
The heavy bond exists so that they are not.

Fitted, the bond sits in *parallel* with that board-and-USB path between the two V− nodes, and current divides by resistance.
Two metres of 8 AWG is about 0.005 Ω, one-way at 75 °C.
That is half of §5.1's 0.0052 Ω/m, because that column is the loop and a bond is a single conductor, not a pair.
Several metres of 26 AWG plus the USB cabling and the hub is on the order of 0.7 Ω.
That is a ratio near 135 : 1, so over 99 % of any circulating or equalising current between zones flows in the bond and next to none of it in the signal grounds.
That is the whole reason to fit it.

**What the bond does not do is protect you against a GND feeder that comes loose at its supply.**
Be clear about that failure, because it is the dangerous one.
With the feeder open, the zone's return current has to reach its supply through the board grounds and the USB ground *and then* through the bond, so there the bond is in series with the thin wire, not in parallel with it, and nothing shunts the 24-26 AWG.
No fuse sees it either: the branch fuses sit on the +5 V side, the main is sized for tens of amps, and the path's own resistance holds the current to a couple of amps - which then sits there indefinitely, with no symptom but dim LEDs.
The defences against that failure are mechanical and procedural rather than electrical: ferrules or ring lugs on every GND feeder termination, screws torqued down properly, strain relief so a tug cannot load a terminal, the §11 continuity checks before anything is energised, and never letting one board span two supplies.

---

## 6. Diagram 4 - Power distribution, sizing, fusing and injection

![Fanning one 5 V supply out to four runs through a main fuse, bus bars and branch fuses, with sizing tables and injection guidance](diagrams/04-power-distribution.svg)

### 6.1 The fan-out pattern

Supply V+ → that feeder's main fuse → its +5 V bus bar → one branch fuse per run → that run's red leads.
Supply V− → that feeder's GND bus bar → that run's black leads.
Data and its ground arrive separately, from the Fadecandy.

How many red and black leads a run has is the Option A / Option B choice in §6.3: two of each, one pair to each end of the run, for full brightness, or one of each on the DI-end pigtail under a stated brightness cap.
Everything else on this page is identical either way, including every gauge and every fuse rating.

One supply can feed more than one distribution block, and §9.2 does exactly that.
Each block is its own feeder: its own pair of conductors off the supply terminals, its own main fuse, its own bus bars.
Nothing is shared between two feeders except the supply terminals they land on.

The bus bars carry every run at once, so they are sized for the cluster total rather than for one run.
§6.2 is the single place that says how, and it makes the bus a **bar or a listed distribution block**, not a length of hookup wire with taps on it.
Read the feeder gauge, the main fuse and the branch details for your run count straight off the tables there.

The feeder is the conductor that carries the whole zone current over the whole distance, so it is the one whose length sets the drop and the one §6.2 sizes most carefully.
The main fuse goes at the **supply end** of the feeder, so that one fuse protects the feeder and the bus bars together.
Keep the unfused stub between the supply terminal and that fuse as short as you physically can, because nothing protects it.
The GND bus is never smaller than the +5 V bus, and the GND feeder is never thinner than the +5 V feeder.
All the current that goes out comes back.

### 6.2 Sizing and fusing - the whole policy, in one place

> **Where these numbers come from.**
> Every gauge, fuse rating and length in this section is derived here from four published inputs and nothing else: the conductor table in §5.1, the standard fuse ratings of NEC 240.6(A), the continuous-load rule of NEC 210.19(A)(1)(a) and 210.20(A), and the voltage-drop guidance of NEC 210.19(A) Informational Note No. 4.
> The arithmetic is shown at every step, so you can repeat it for a layout this document does not cover instead of interpolating.
> One input is still unmeasured, and it is not a conductor: the rail resistance inside your particular strips. §6.3 is where that is settled, and it changes only how many points a run is fed at and at what brightness. It changes nothing about what cable or fuses to buy.

A fuse protects the **conductor downstream of it**, not the LEDs.
A 60 A supply will happily push 60 A into a shorted 16 AWG branch, and 16 AWG will not survive that.
The fuse is what stops it becoming a fire.
Note that the supply's own current limit is not a substitute: it will sustain tens of amps into a fault indefinitely and call that normal operation.

#### The floor, and the four places it is spent

The WS2812B is characterised from 4.5 V to 5.5 V (§2.4).
**4.5 V is a floor at the dimmest LED on a run, not at the strip's solder pads**, and getting that distinction wrong is what made an earlier draft of this document optimistic by a factor of two.
The pads are not the end of the circuit; the strip's own rails carry current too, and they drop.

§2.4 fixes the top of the budget as hard as the datasheet fixes the bottom: the supply is set to 5.00 V and stays there, because winding the trimpot up to cover drop raises `VIH` faster than it raises the supply.
So the entire budget is the 0.50 V between those two numbers, and it is spent in four places:

```
supply terminals, set to 5.00 V under load                    5.000 V
    feeder        1.5 %    0.075 V     whole zone current
+5 V bus bar and GND bus bar                                  4.925 V
    bus           0.5 %    0.025 V     whole zone current
branch tap on the bus                                         4.900 V
    branch        3.0 %    0.150 V     one run, 3.84 A
strip solder pads                                             4.750 V
    strip's own rails      0.250 V     §6.3, at that option's design point
dimmest LED on the run                                        4.500 V
```

Those five voltages are the specification.
Every gauge, length and fuse below exists to hold them, and §11 meters them one by one.

**Why the budget splits that way.**
The 5 % that reaches the pads is not invented here: NEC 210.19(A) Informational Note No. 4 recommends a branch circuit drop of no more than 3 % and a total of no more than 5 % across feeder and branch together, for reasonable efficiency of operation.
This system maps onto that directly.
The branch takes the full 3 %, because it is the segment whose length you have the least freedom over: the runs are where the runs are.
The feeder and the bus share the remaining 2 %, and they share it unevenly, 1.5 % to 0.5 %, because a bus bar's resistance can be driven almost to zero for a few dollars while a feeder's cannot.
The remaining 0.25 V belongs to the strip, and unlike the other three it is not a conductor you choose. It is fixed by the strip you already own and by how many points you feed it at.

#### The policy, in one direction

The four rules an earlier draft used were stated as a range, and a range lets you pick the wrong end of it.
These are five steps in one direction, and every row of every table below is the result of running them.

1. **Load.** `I = runs × 3.84 A` for a feeder or a bus, `3.84 A` for a branch. Full white, every pixel, worst case. The power governor in the controller is software and is not allowed to be load-bearing for anything with a fuse in it.
2. **Design current.** `I_design = 1.25 × I`. Lighting that stays on for three hours or more is a continuous load in the sense of NEC Article 100, and NEC 210.19(A)(1)(a) and 210.20(A) size both conductor and overcurrent device to 125 % of it. The same factor does a second job for free: 1 ÷ 1.25 is 80 %, which is the continuous derate a blade fuse wants anyway, since its rating is established at 23 °C and it is going in a warm enclosure.
3. **Fuse.** The **smallest** NEC 240.6(A) standard rating that is at or above `I_design`. Standard ratings are 1, 3, 6, 10, 15, 20, 25, 30, 35, 40, 45, 50 and 60 A. Smallest, not any rating in a range: a bigger fuse protects nothing extra and protects the wire less.
4. **Conductor.** Ampacity at or above the **fuse rating**, from the §5.1 table. Not at or above the load, and not at or above the design current. The fuse is what actually limits what can flow, so the conductor has to survive whatever the fuse will pass indefinitely.
5. **Then voltage drop, which almost always wins.** Steps 1 to 4 stop conductors overheating and say nothing about drop. Compute `V = I × R_loop × L` at that segment's own current and check it against that segment's allowance above. At 5 V this normally demands a gauge two or three sizes above what step 4 asked for, and it is the step that sets the lengths.

And one rule about where fuses go, which follows from step 4:

**A fuse is required wherever the conductor gets smaller.**
The main fuse sits at the supply end of the feeder and protects the feeder and the bus.
A branch fuse sits at every bus tap and protects that branch.
There is nowhere else in this system that copper steps down, and so nowhere else that needs a fuse.

#### Feeder and main fuse, worked out for 1 to 8 runs

Steps 1 to 4 give the fuse and the minimum gauge.
Step 5 gives the maximum length, and it is a length **per gauge**, because a gauge without a length is not a specification.
Pick any gauge at or right of the minimum, and keep the feeder inside the length in its column.

| Runs | Load | ×1.25 | Main fuse | Min AWG | 16 AWG | 14 AWG | 12 AWG | 10 AWG | 8 AWG | 6 AWG | 4 AWG | 2 AWG |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3.84 A | 4.80 A | 6 A | 16 | 0.59 m | 0.93 m | 1.48 m | 2.36 m | 3.79 m | 6.06 m | 9.66 m | 15.3 m |
| 2 | 7.68 A | 9.60 A | 10 A | 14 | - | 0.47 m | 0.74 m | 1.18 m | 1.89 m | 3.03 m | 4.83 m | 7.67 m |
| 3 | 11.52 A | 14.40 A | 15 A | 14 | - | 0.31 m | 0.49 m | 0.79 m | 1.26 m | 2.02 m | 3.22 m | 5.11 m |
| 4 | 15.36 A | 19.20 A | 20 A | 12 | - | - | 0.37 m | 0.59 m | 0.95 m | 1.52 m | 2.42 m | 3.84 m |
| 5 | 19.20 A | 24.00 A | 25 A | 10 | - | - | - | 0.47 m | 0.76 m | 1.21 m | 1.93 m | 3.07 m |
| 6 | 23.04 A | 28.80 A | 30 A | 10 | - | - | - | 0.39 m | 0.63 m | 1.01 m | 1.61 m | 2.56 m |
| 7 | 26.88 A | 33.60 A | 35 A | 8 | - | - | - | - | 0.54 m | 0.87 m | 1.38 m | 2.19 m |
| 8 | 30.72 A | 38.40 A | 40 A | 8 | - | - | - | - | 0.47 m | 0.76 m | 1.21 m | 1.92 m |

A dash means the fuse on that row would exceed that gauge's ampacity, so the gauge is not permitted there whatever its length.

Read the eight-run row before anything else, because it is the one that decides your layout.
Even 6 AWG only reaches 0.76 m, and 4 AWG only 1.21 m.
**At 5 V there is no such thing as a long feeder.** The supply belongs on the same board as the distribution block it feeds, close enough to reach with a 0.3 to 0.5 m pair. That is not a preference, and the subsection after next works through what it saves.

Two notes on the fuses themselves, because the rating is only half of a fuse:

- **The holder has to be rated for it too.** An inline blade-fuse holder on 16 AWG pigtails is a common way to buy a "40 A fuse" and end up with 16 AWG in series with your 8 AWG feeder. Anything above about 30 A wants a bolt-down holder, ANL or MIDI class, landed with ring lugs.
- **One main per feeder, not per supply.** A supply with two feeders leaving its terminals gets two mains, one per feeder, each read off its own row.

The table stops at 8 runs because that is the largest zone in this document and one Fadecandy drives eight channels.
**Do not put more than 8 runs on one bus.** Split the zone instead and give the second block its own feeder, its own main fuse and its own bus bars.

#### The bus: a bar, not a wire

The bus is the one place where the cheapest correct answer is also the simplest, so this document specifies hardware rather than a gauge.

**Use a copper bar, or a listed power distribution block, with a continuous rating at or above the main fuse for your run count.
One for +5 V and one for GND.
Land the feeder at the bar's centre, not at one end, and take the branch taps off either side of it.**

That single rule is worth more than a gauge column, and here is the arithmetic that says so.
**The arithmetic below assumes copper**, which is what the rule specifies; brass is treated separately at the end of it.
The bus allowance is 0.025 V at up to 30.72 A.
A modest bar of 20 mm² section, about 2 × 10 mm and smaller than any marine busbar you can actually buy, has a resistance of 0.86 mΩ per metre in copper.
Centre-fed with the taps spread along it, each half carries at most half the zone current and tapers to nothing at the end, so a 1 m bar drops `30.72 × 0.00172 × 1 ÷ 8` = 6.6 mV across both bars together.
Allow another 9 mV for three lug joints at 0.1 mΩ in the worst path and the bus has spent 16 mV of its 25 mV.
Feeding the same bar from one end instead of the centre quadruples that to 26 mV and blows the allowance on its own, which is why the centre feed is in the rule.

**Brass is about four times as resistive as copper, so it does not inherit any of that.**
The same 20 mm² bar in brass is roughly 3.4 mΩ/m, and at 1 m centre-fed it spends about 26 mV across both bars - the entire 25 mV allowance on its own, before a single lug joint, and the same figure the end-fed copper case is rejected for.
Brass only fits if the bar is short: at 0.2 m it spends about 5 mV, which leaves room for the joints.
Buy copper and the length stops being a question you have to ask.

The reason not to use wire is not that the arithmetic is harder.
It is that a "bus bar" made of wire has to be sized for the full zone current at the fuse's ampacity, which at eight runs means a piece of 8 AWG a few hundred millimetres long with seven taps soldered or crimped onto it, and every one of those taps is an unsupported joint carrying up to 30 A.
A $10 busbar removes the joints, removes the drop, removes the ampacity question, and removes a column of numbers from this document that had no business being here.

#### Branch: one run, 3.84 A

Every branch in this system carries the same current, so there is one row to remember rather than a table indexed by anything.

- **Branch fuse: 6 A, one per run.** `1.25 × 3.84 = 4.80 A`, and 6 A is the smallest NEC 240.6(A) standard rating at or above it. That is the whole derivation, and 6 A is the answer; 5 A is not a standard rating in that list and is not an alternative here.
- **One fuse per run, feeding both of that run's injection legs.** Under Option A in §6.3 a 64-pixel run is fed at both ends. Both legs come off the same fuse, so the fuse still sees only that run's 3.84 A.

Each leg is sized as though it carried the **whole** 3.84 A, not half of it.
The split between the two legs depends on their relative lengths, which differ for every run and which you are not going to calculate.
A leg can never carry more than the whole run, so sizing every leg for the whole run makes the answer independent of routing, and it stays correct if you ever feed a run from one end only.

| Branch leg | Ampacity vs the 6 A fuse | Max length, per leg |
|---|---|---|
| 16 AWG | 8 A, ok | 1.17 m |
| 14 AWG | 15 A, ok | 1.87 m |
| 12 AWG | 20 A, ok | 2.96 m |
| 10 AWG | 30 A, ok | 4.72 m |

**14 AWG to 1.87 m is the default**, and it is what §9 assumes.
Measure each leg separately from the fuse output to the strip pad it lands on; both have to fit on their own.
Note that 14 AWG at a round 2 m does not fit: it is 0.161 V against a 0.150 V allowance. An earlier draft published 2 m, having computed it from solid-conductor resistance at 20 °C rather than stranded at 75 °C.

#### Before you buy 4 AWG: the lever is length, not copper

Those feeder gauges are not a law of physics.
They are what it costs to feed a whole zone from a supply that is not sitting in it.

The feeder's drop is `I × R_loop × L`, and two of those three terms are yours to choose.
Buying your way down the resistance column is the most expensive way to fix either of the others, and past a point it stops working at all.
Here are the same three zones as §9.2, sized at three feeder lengths, read straight off the table above.

| Zone | Feeder at 1 m | at 0.5 m | at 0.3 m |
|---|---|---|---|
| Living room, 8 runs, 30.7 A | 4 AWG | 6 AWG | 8 AWG |
| Bedroom, 6 runs, 23.0 A | 6 AWG | 8 AWG | 10 AWG |
| Kitchen / hall, 4 runs, 15.4 A | 6 AWG | 10 AWG | 12 AWG |

Read across the kitchen row.
Nothing about the load changed, and the feeder went from 6 AWG to 12 AWG, a quarter of the cross-section, purely by moving the supply 0.7 m closer.
Four and 6 AWG need crimped lugs and a hydraulic crimper to terminate properly; 10 and 12 AWG land in an ordinary screw terminal with a ferrule.
The difference between those two build experiences is 700 mm of siting.

**This is the same recommendation §5.3 already makes for data, for the same reason: it is cheaper to move the source than to pay for the distance.**
A third 5 V supply costs far less than a reel of 4 AWG and the afternoon spent pulling and terminating it, and §9.2 recommends a third supply anyway, for headroom.
So treat the table above as the sizing consequence of a layout decision, and make the layout decision first.

### 6.3 Power injection: how many points a run needs

> **This section reverses what an earlier version of it concluded.**
> It used to say a single 64-pixel run does not need far-end injection. At full white it does. The old thresholds assumed 5.00 V at the strip when §6.2 spends 0.25 V reaching it, so all of them were 2× too generous, and correcting them moves the answer from "probably not needed" to "not physically possible from one end".
> **At full white, every run is fed at both ends.** The one thing that changes that is a brightness cap, and this section works out exactly what cap buys you what. The rest of it derives both answers rather than asserting them, because you should be able to check them.

**Do not use an "inject every 2 m" length rule here.**
That number is stated for 60 LEDs/m strips, and these are 30 LEDs/m.
Length on its own is not the variable that causes the problem; current is, and at half the LED density the same length carries half the current.

A run fed from one end carries all of its current at the feed and none at the far tip, so the average current in the strip's own rail is half the total.
That halving does not survive on its own, because the current goes out along the +5 V rail and comes back along the GND rail, and both of them drop.
The factor of two for the two rails cancels the factor of one half from the averaging - `2 × (I_total/2) × r_rail × L = I_total × r_rail × L` - which gives the half-length rule for a uniformly loaded strip:

```
V_drop = I_total × r_rail × L       at the far tip, for a run fed from ONE end
                                    r_rail = Ω/m of ONE rail inside the strip
```

The product `I_total × L` is what actually sets the drop, so compare in amp-metres rather than metres:

| Case | Pixels | Current | Length | `I × L` |
|---|---|---|---|---|
| 2.0 m of 60 LEDs/m, the case the "2 m" rule is written for | 120 | 7.20 A | 2.00 m | 14.4 A·m |
| **your run: 64 px at 30 LEDs/m** | **64** | **3.84 A** | **2.11 m** | **8.10 A·m** |

Your run sits at 56 % of that threshold.
That kills the "2 m rule" as a reason to worry, but it does not license feeding a run from one end, and the rest of this section is why.

**The feed at the pads is 4.75 V, not 5.00 V.**
§6.2 spends 0.25 V of the budget getting there, and the strip's own rails have the remaining **0.250 V** to play with before the dimmest LED reaches its 4.5 V floor.
Working the thresholds from 5.00 V at the pads, as an earlier draft of this section did, doubles every one of them.

Where the dimmest LED is depends on how many points you feed the run at.
A span of strip fed from one end and loaded uniformly drops `I_span × r_rail × L_span`, and adding injection points cuts both `I_span` and `L_span` at once, so the drop falls with the **square** of the number of singly-fed spans:

| Injection points | Singly-fed spans | Drop at the dimmest LED | `r_rail` that fits 0.250 V |
|---|---|---|---|
| one end only | 1 × 2.11 m at 3.84 A | `8.10 × r_rail` | ≤ 0.031 Ω/m |
| **both ends** | 2 × 1.055 m at 1.92 A | `2.03 × r_rail` | **≤ 0.123 Ω/m** |
| both ends + midpoint | 4 × 0.527 m at 0.96 A | `0.51 × r_rail` | ≤ 0.494 Ω/m |

**So a 64-pixel run at full white must be fed at both ends.**
That is not a judgement call about your strips.
One-end feeding at full white needs `r_rail` at or below 0.031 Ω/m, which is about 0.55 mm² of copper in each rail: 8 mm of 2 oz foil, or 16 mm of 1 oz.
A 10 mm wide strip does not have that and is not built that way, so one-end feeding at full white is off the table for any strip you can buy.

Feeding both ends relaxes the requirement four-fold, to 0.123 Ω/m.
That is about 0.14 mm² per rail, which is a 2 mm rail in 2 oz copper: a real strip, and that is what makes both-ends injection the design at full brightness rather than a contingency.
But read it as the threshold it is and not as comfort, because **the same 2 mm rail in 1 oz copper is about 0.25 Ω/m and does not fit.**
The difference between a strip that holds this budget and one that misses it by double is a foil weight you cannot see, which is the whole reason the next paragraphs are about measuring rather than assuming.
(Those two figures are construction estimates from copper resistivity at room temperature, not measurements of your strips.)

#### What feeding both ends costs you to build

Say this plainly before going further, because it is an evening of work and it appears in none of the tables above.

**Your runs have solder pads at the DI end only.**
§1 records the pigtail as three leads all at that end, and the far end as unterminated inside a sealed silicone sleeve.
The second pair of legs therefore does not exist yet.
Creating it means opening the sleeve at the far end of **each of about 18 runs**, soldering a red lead to the strip's +5 V rail and a black lead to its GND rail, and resealing the sleeve around them.
That is roughly 18 sleeve openings, 36 solder joints onto strip rails, and 36 more terminations at the distribution blocks.
§9.1 lists the consumables it needs and §11 carries it as a build step.

There is exactly one lever that removes all of that work, and the next subsection is it.

#### The two ways to build this, and what each demands of the strip

Both are definite, and the choice is yours.
Make it before you buy leads.

**Option A - full brightness, fed at both ends.**
Two red legs and two black legs per run, both reds off that run's single 6 A branch fuse.
The strip must have `r_rail` at or below **0.123 Ω/m**.
Costs the ~18 sleeve openings above.

**Option B - the DI end only, under a stated brightness cap.**
One red leg and one black leg per run, on the pigtail that is already there.
No sleeve work at all.
Fed from one end the drop at the far tip is `V = 8.102 × cap × r_rail` against the same 0.250 V allowance, so the cap you set decides the strip you need:

| Brightness cap | Drop at the far tip | `r_rail` that fits 0.250 V |
|---|---|---|
| 100 % | `8.102 × r_rail` | ≤ 0.031 Ω/m - no flexible PCB strip achieves this |
| 50 % | `4.051 × r_rail` | ≤ 0.062 Ω/m |
| 30 % | `2.431 × r_rail` | ≤ 0.103 Ω/m |
| **25 %** | `2.026 × r_rail` | **≤ 0.123 Ω/m** |

**A 25 % cap fed from one end asks exactly the same of the strip as full white fed from both ends: 0.123 Ω/m, the same number to three figures.**
That is arithmetic rather than coincidence.
Feeding both ends quarters the drop by halving the current and the length together; a 25 % cap quarters it by taking the current to a quarter on its own.
§7 already puts realistic ambient light for this installation at about 25 %, so for the way you actually intend to use these lights, Option B asks no more of the strip than Option A does.

**Option B is reversible and wastes nothing.**
If you later decide you want full white, the far ends go dim and shift warm, you see it the moment you try it, and you do the sleeve work then.
Every lead, fuse, block and metre of cable bought for Option B is still exactly the right part afterwards.

**Neither option changes a single gauge or fuse rating in §6.2.**
Everything there is sized for full white whether you ever run full white or not, because the cap is software and a fuse protects against the case where the software is wrong.
The cap changes only the strip-internal drop, which is a question about how the light looks and not about what can overheat.

**Which leaves exactly one thing to measure.**
Substituting into the tables above, against the 0.250 V the strip is allowed:

| `r_rail` (Ω/m, one rail) | one end, full white | one end, 30 % cap | **A: both ends, full white, or B: one end at 25 %** | both ends, 30 % cap |
|---|---|---|---|---|
| 0.05 | 0.41 V | 0.12 V | **0.10 V** | 0.03 V |
| 0.10 | 0.81 V | 0.24 V | **0.20 V** | 0.06 V |
| 0.15 | 1.22 V | 0.36 V | **0.30 V** | 0.09 V |
| 0.20 | 1.62 V | 0.49 V | **0.41 V** | 0.12 V |
| 0.30 | 2.43 V | 0.73 V | **0.61 V** | 0.18 V |

Read the bold column: at either option's design point, a strip up to about 0.12 Ω/m holds the budget and a worse strip than that does not.
Read the outer columns for what a further half-step of brightness buys you if it turns out you have a bad strip.

> **Unverified:** `r_rail` for your specific silicone-sleeved strips is not known and was not established for this document.
> It depends on how that particular strip was built, and these were cut and sleeved by a previous owner.
> Do not substitute a generic figure. Measure it, or measure the drop directly, as below.

**Settle it by measurement, not by argument.**
You do not need `r_rail` itself. You need the voltage at the dimmest LED, and that is directly meterable:

1. Wire one run the way you have chosen to build all of them: Option A with both legs fitted, or Option B with the DI-end pigtail alone.
2. Drive that one run at the brightness its option assumes - full white for Option A, your chosen cap for Option B. One run at full white is 3.84 A, which is inside any sensible power-governor budget, so you do not have to defeat the clamp to do this.
3. Meter DC volts at the strip's **solder pads**. Then meter between the strip's own +5 V and GND rails at its dimmest point, which is **the midpoint of the run under Option A** and **the far tip under Option B**.
4. **The difference between those two readings must be 0.250 V or less.** If it is, the strip fits its allowance and you are done.

Take the difference, not the second reading on its own. With a single run lit the feeder and the bus are barely loaded, so the whole strip floats about 0.09 V above where it will sit once the zone is full, and a dimmest point that reads 4.50 V under those conditions will not still read 4.50 V with eight runs on. The difference is a property of the strip and does not move with load; the absolute floor is checked later, in §11 step 19, with the whole cluster lit.

If the strip's own drop exceeds 0.250 V under the option you chose, you have three answers, in order of what they cost you:

- **Lower the brightness cap.** Drop scales directly with current, so halving the cap halves the drop. This is ambient apartment lighting, and 25 % of 64 WS2812Bs is already a lot of light for a room.
- **Feed more points.** From Option B that means moving to Option A, which is four times looser. From Option A it means a third injection point at the midpoint, which by the first table takes the requirement out to 0.494 Ω/m and settles the question for any strip at all. Each extra point is another sleeve opening.
- **Accept it at full white only**, knowing the dimmest part of the run will be slightly dim and slightly warm in colour when you drive it hard, and normal at everything below that.

---

## 7. Current and cable arithmetic you can reuse

State the assumptions, not just the results.

**Assumptions.** Each WS2812B contains three LED dies driven by internal constant-current sinks at a nominal 20 mA per colour channel, so a pixel at full-scale white draws about 60 mA.
This is the standard figure used for sizing NeoPixel-class installations.
It is a ceiling: it assumes all three channels at 255 simultaneously, on every pixel, at once.

```
per pixel      3 × 20 mA                    = 60 mA
per run        64 px × 60 mA                = 3.84 A
per run        3.84 A × 5 V                 = 19.2 W
8 runs         8 × 3.84 A                   = 30.7 A   = 154 W
12 runs        12 × 3.84 A                  = 46.1 A   = 230 W
18 runs        18 × 3.84 A                  = 69.1 A   = 345 W
```

**Supply headroom.** A supply gets the same 80 % continuous derate as everything else with a rating on it, for the same reason: full white for an evening is a continuous load, not a surge, and these are open-frame units going into a closed enclosure.
So a supply may carry `runs × 3.84 A` up to 80 % of its rating, and no further.
That works out at **6 runs on the S-150-5 and 12 runs on the S-300-5**.

| Runs | Worst case | On the S-150-5 (30 A) | On the S-300-5 (60 A) |
|---|---|---|---|
| 6 | 23.0 A | 77 % - at the limit | 38 % - comfortable |
| 8 | 30.7 A | 102 % - **do not** | 51 % - comfortable |
| 12 | 46.1 A | over - no | 77 % - at the limit |
| 18 | 69.1 A | over - no | over - split 6 + 12 across both, per §9.2 |

Two things follow, and they are the reason this number matters.
**Your 30 A supply cannot carry eight full-white runs**, so the eight-run starter cluster goes on the 60 A unit and the 30 A unit is kept for a second zone.
And 6 + 12 is exactly 18, which means your two supplies carry the full build with nothing left over: both sit at 77 % of rating, at the ceiling rather than inside it.
A third supply is what buys margin back, and §9.2 says where it goes.

**What it costs to run.** You asked about the electric bill, so:

```
18 runs at full white       345 W      this never actually happens
18 runs, ambient at ~25 %   ~86 W      realistic for warm indirect light
86 W × 5 h/day × 30 days    ~13 kWh/month
```

At a typical US residential rate that is a couple of dollars a month.
The thing worth watching is idle draw: a WS2812B pulls roughly a milliamp even when all three channels are zero, so 1152 pixels sitting "off" is on the order of 1 A, or about 6 W, plus the supplies' own no-load loss.
Put the supplies behind a switch or a smart plug, and the standby cost goes away.

*(The per-pixel quiescent figure is approximate and is not from the datasheet excerpt used here; treat ~6 W standby as an estimate, and measure it once you have a cluster built.)*

---

## 8. Diagram 5 - Multi-board expansion

![Three Fadecandy boards on a powered USB hub, with fcserver addressing each board by USB serial number](diagrams/05-multi-board.svg)

18 runs needs 18 channels, so three boards: 24 channels available, 18 used.

**Use a powered hub.** Do not hang three boards off the Pi's own ports.
The Pi 3B+ shares roughly 1.2 A across all four of its USB ports - a figure that is widely cited in Raspberry Pi community sources but which was **not confirmed against official Raspberry Pi documentation** for this document.
The recommendation does not depend on the exact number: a hub with its own supply removes the question entirely, and it keeps a brown-out on one board from taking the Pi down with it.

**Bandwidth is not your constraint.** Each Fadecandy is a full-speed 12 Mbps USB device.
Three of them together are nowhere near saturating a USB 2.0 bus.
Cable length is the constraint: 5 m per passive segment, and a powered hub starts a fresh segment.

### 8.1 How fcserver addresses each board

A device entry with no `serial` key matches any Fadecandy.
That is fine with one board and ambiguous with three, so name them:

```json
{
  "listen": [null, 7890],
  "verbose": true,
  "color": { "gamma": 2.5, "whitepoint": [1.0, 1.0, 1.0] },
  "devices": [
    { "type": "fadecandy",
      "serial": "FFFFFFFFFFFF00180017200214134D44",
      "map": [ [ 0, 0,    0, 512 ] ] },

    { "type": "fadecandy",
      "serial": "FFFFFFFFFFFF0021003B200314134D44",
      "map": [ [ 0, 512,  0, 256 ] ] },

    { "type": "fadecandy",
      "serial": "FFFFFFFFFFFF00340052200914134D44",
      "map": [ [ 0, 768,  0, 384 ] ] }
  ]
}
```

The serials above are examples taken from the upstream documentation.
Yours will differ.

A map entry is `[ OPC_channel, first_OPC_pixel, first_device_pixel, count ]`.
A Fadecandy's own pixels are numbered 0 through 511: strand 1 starts at index 0, strand 2 at 64, strand 3 at 128, and so on.
So `count: 256` on board B means its first four strands, runs 9 to 12, and `count: 384` on board C means its first six strands, runs 13 to 18.
The three entries cover OPC pixels 0-511, 512-767 and 768-1151: 1152 pixels, which is 18 runs of 64.

**Finding the serials.** Plug in one board at a time and run `fcserver` with `"verbose": true`.
It prints the serial of each device it opens.
Write it on a label, stick it to that board, and only then plug in the next one.

**Why serials and not port order.** USB enumeration order is not stable.
Address boards by port and your living room becomes your bedroom after a reboot.
Address by serial and a board keeps its zone whichever socket it lands in.

> `"listen": [null, 7890]` makes fcserver reachable from other machines on your network, which is what the Android app needs. Upstream warns that fcserver has no built-in security: anyone who can reach that port controls the lights. Keep it on your own LAN, not exposed to the internet.

---

## 9. A worked example for the apartment

### 9.1 Start here: 8 runs, one board, one supply

This is a sensible first installation.
It is a complete, working system, and it is also the first cluster of the full build, so nothing is wasted if you expand.

| | |
|---|---|
| Runs | 8, one per Fadecandy channel 0-7 |
| Pixels | 512 |
| Worst-case current | 30.7 A |
| Supply | **sompom S-300-5 (60 A)** - 51 % loaded, comfortable |
| Bus bars | Bar or listed block rated 40 A or better, +5 V and GND alike, fed at the centre |
| Feeder | 8 AWG under 0.47 m, or 6 AWG under 0.76 m - 0.075 V at 30.7 A |
| Main fuse | 40 A at the supply end of the feeder - the 8-run row of §6.2 |
| Branch fuses | 6 A per run, 8 of them |
| Branch wire | 14 AWG, each leg under 1.87 m - 0.150 V at 3.84 A. **Option A: two legs per run. Option B: one.** See below |
| Far-end termination | **Option A only.** 8 sleeve openings, 16 solder joints onto strip rails, and the consumables below |
| Data | 8 × twisted DATA + GND pairs, 24-26 AWG, each under 5 m |
| Host | Pi 3B+, its own 5 V 2.5 A supply, one USB cable to the board |

Note the supply choice: the 30 A unit would be at 102 % of rating here.
Use the 60 A unit for this cluster.

**Decide Option A or Option B before you order, because it changes the lead count and nothing else.**
§6.3 has the derivation; the short version is that full brightness needs both ends fed, and a 25 % brightness cap on the DI-end pigtail alone asks exactly the same of the strip.
Option A doubles the branch wire and the block terminations for this cluster, and it adds real bench work:

| Option A also needs | Why |
|---|---|
| A sharp blade and patience | Opening the silicone sleeve at the far end of each of the 8 runs without nicking the strip |
| Flux and a fine-tip iron | 2 joints per run onto the strip's own +5 V and GND rails, 16 in all |
| Adhesive-lined heatshrink, or clear RTV silicone | Resealing each sleeve around the two new leads. Do not leave a bare rail in a kitchen or a bathroom |
| 8 more 14 AWG legs of each colour | The second pair per run, plus 16 more ferrules for the block |

Option B needs none of that row and stays reversible: if you later want full white, you do the sleeve work then and nothing bought for Option B is wasted.

### 9.2 Expansion path: 18 runs, three boards, both supplies

18 runs at full white is 69.1 A, and your two supplies total 90 A, so it fits.
The binding constraint is the 30 A unit: the 80 % continuous ceiling in §7 puts it at 6 runs (23.0 A, 77 %), and that is exactly as far as it goes.

The version that fits the two supplies you already own:

| Supply | Runs | Worst case | Load |
|---|---|---|---|
| S-300-5 (60 A) | 12 | 46.1 A | 77 % |
| S-150-5 (30 A) | 6 | 23.0 A | 77 % |

Mapped onto rooms and boards:

| Zone | Runs | Worst case | Supply | Board and channels |
|---|---|---|---|---|
| Living room | 8 | 30.7 A | S-300-5 | Fadecandy A, ch 0-7 |
| Kitchen / hall | 4 | 15.4 A | S-300-5, same unit | Fadecandy B, ch 0-3 (ch 4-7 unused) |
| Bedroom | 6 | 23.0 A | S-150-5 | Fadecandy C, ch 0-5 |

Two things about that table are worth calling out.

First, **no board spans two supplies**, and that is deliberate rather than tidy.
Board B drives the four kitchen/hall runs and leaves ch 4-7 unused instead of picking up part of the bedroom, and the bedroom is board C's alone.
A board whose channels straddle two supplies is the single component bridging two power zones, and the wires doing the bridging are its 24-26 AWG grounds - which §5.3 rules out.
Spare channels are cheap; that bridge is not.
It does not remove the shared reference, because all three boards still share ground through the hub and the Pi over USB - which is exactly why the V−-to-V− bond in §5.3 is required and not optional, and why it is sized to take that current instead of the signal grounds.

Second, living room and kitchen/hall share one supply, which only respects the clustering rule in §5.3 if those two areas are adjacent enough for that supply to sit within a metre or two of both of the blocks it feeds.
If they are not, that shared supply is exactly the long-power-run mistake §5.1 warns about.

Those two zones get a distribution block each, not one block between them.
Do not run a single shared feeder from the S-300-5 to both: that conductor would carry the combined 46.1 A, which needs a 60 A main, 4 AWG to carry it, and still reaches only 0.81 m.
Land two separate feeders on its V+ and V− terminals instead, one per block, each with its own main fuse at the supply end, so no conductor ever carries more than the runs behind it.
Read each zone's feeder and main fuse off the §6.2 table by its run count.
Every zone's bus is the same thing regardless of size: a bar or listed block rated at or above that zone's main fuse, fed at its centre.

| Zone | Main fuse | Feeder, and how far it reaches |
|---|---|---|
| Living room, 8 runs, 30.7 A | 40 A | 8 AWG to 0.47 m, or 6 AWG to 0.76 m |
| Bedroom, 6 runs, 23.0 A | 30 A | 8 AWG to 0.63 m, or 6 AWG to 1.01 m |
| Kitchen / hall, 4 runs, 15.4 A | 20 A | 10 AWG to 0.59 m, or 8 AWG to 0.95 m |

Every one of those feeders has to reach its block within the length beside it, so site each supply accordingly - that length is a sizing constraint, not a preference.

Both supplies sit exactly on the 80 % ceiling rather than inside it, and the shared-supply compromise above exists only because you have two supplies for three zones.
A third 5 V supply, one per zone, removes both problems at once: every supply lands near 50 %, and every supply is genuinely local to the runs it feeds.
That is cheaper than rewiring for heavier bus bars, and it is the layout §5.3 actually recommends.

Keep each supply physically inside the cluster it serves, and bond the two supplies' V− terminals to each other with 8 AWG as §5.3 requires.

---

## 10. Safety

The two supplies you have are open-frame units.
Their mains terminals are exposed screw terminals on an uninsulated board.
They are components intended to be built into equipment, not appliances.
Treat them accordingly.

**Enclosure.** Each supply goes in a closed enclosure before it is ever plugged in.
No exposed mains terminals, ever, not even "just for testing."
A metal or flame-rated plastic box, with the supply's mains end behind a barrier or a terminal cover, and cable glands or strain relief where the leads enter and leave.

**Earthing.** These are Class I supplies with an earth terminal, marked with the earth symbol.
The mains earth conductor lands on that terminal, and if the enclosure is metal, the enclosure is bonded to it too.
Do not run them from a two-wire cord with the earth left off.
If a live conductor works loose inside an unearthed metal box, the box becomes live and nothing trips.

**Mains fusing and switching.** Feed each supply through a fused inlet or an in-line fuse sized from the supply's own AC input rating, which is printed on its label - read the label rather than calculating it.
Put a switch on the mains side so the whole installation can be isolated without unplugging anything.

**Strain relief.** Mains conductors must not be able to move if a cable is tugged.
Anchor the mains cable at the enclosure wall so any pull is taken by the gland, not by the screw terminals.

**Separation.** Keep mains wiring physically separated from the 5 V and data wiring inside any shared enclosure - separate compartments or a barrier, different cable entries, and never in the same bundle.
This is a safety requirement first; the reduced noise on the data lines is a bonus.

**Bonding between supplies.** If the installation uses more than one supply, every supply's V− terminal is bonded to every other with a conductor sized like the largest zone's feeder - 8 AWG in the §9.2 layout.
Fit those bonds before any supply is energised.
The boards and the USB hub already tie the supply grounds together through 24-26 AWG signal wire; the bond is what keeps that thin path from being the only tie, so that circulating current between zones flows in copper rated for it.
It does **not** protect against a GND feeder coming loose at its supply - see §5.3 for why, and for what does.

**Polarity.** Confirm V+ and V− with a meter before connecting a single strip.
WS2812Bs do have some reverse-connection tolerance, but do not spend it.

**Heat.** These supplies are convection or fan cooled.
Do not seal one into an unventilated box, and do not mount it face-down against insulation.
A supply at 77 % load in a warm cupboard runs hot; that is the case where derating the wire ampacity in §5.1 actually matters.

**If any of the above is outside what you are comfortable doing, have the mains side done by an electrician.** The 5 V side is harmless to you and you can build all of it yourself.
The mains side is the part that can kill someone, and it is a small job for someone who does it every day.

---

## 11. Pre-power-on checklist

Do this with the supply **unplugged from the mains** unless a step says otherwise.
You need a multimeter.

**Before you make up a single run**

1. **Decide Option A or Option B from §6.3, and write the answer down.**
    Option A is full brightness with both ends of every run fed: two red legs and two black legs per run.
    Option B is the DI-end pigtail alone under a stated brightness cap, and a 25 % cap asks exactly as much of the strip as Option A does at full white.
    It changes no gauge and no fuse rating anywhere in this document.
    It changes how many leads you cut, how many terminations the block needs, and whether you spend an evening opening sleeves.
2. **Option A only: make up the far end of every run before anything is mounted.**
    Your strips have pads at the DI end and nothing at the far end, so this joint does not exist yet, and it is far easier to make on a bench than up a wall.
    Per run: open the silicone sleeve at the far end, tin the strip's own +5 V and GND rails, solder a red 14 AWG leg to +5 V and a black one to GND, then reseal with adhesive-lined heatshrink or clear RTV silicone so no rail is left bare.
    That is 2 joints per run, and about 36 joints across a full 18-run build.
    Tug-test each joint before you seal it, and check for a solder bridge between the two rails with the meter while you can still see them.
    Skip this step entirely under Option B.

**Before any power at all**

3. Visually inspect every mains-side connection.
    No stray strands, no exposed copper past the terminal, no conductor that can move.
    Earth is landed on the earth terminal.
4. Enclosure is closed.
    Cable entries are strain-relieved.
5. Set the meter to continuity.
    Confirm **no continuity between the +5 V bus and the GND bus** with everything connected.
    If it beeps, you have a short - find it now.
6. Confirm continuity from the GND bus to every run's black lead, and to the Fadecandy's `−` pins for the channels in use.
    This is the common ground; prove it exists.
    **A continuity beeper cannot do the next two checks.** Once the V−-to-V− bond is fitted and the boards are plugged into the hub, every ground in the installation is connected to every other one *somehow* - §5.3 puts the sneak path through the board grounds and USB at roughly 0.7 Ω, far inside any beeper's threshold - so it beeps whether or not the conductor you care about is actually there. Do these two with the bond off and the boards unplugged.
    First, with the V−-to-V− bond **disconnected** and **every board unplugged from USB**, confirm continuity from each block's GND bus to its own supply's V− terminal. That is the only state in which a beep proves that particular feeder exists.
    Then refit the bond and, still with the boards unplugged and every block's GND feeder lifted at its block, meter between the supplies' V− terminals to prove the bond itself.
    Reconnect everything. If you would rather check with it all connected, use the resistance range rather than the beeper and require milliohms: the direct conductor is a few thousandths of an ohm and the sneak path is about 0.7 Ω, so the reading tells them apart even though the beep does not.
    While you are at each GND feeder termination, check it is ferruled or lugged and properly tight; §5.3 explains why that termination is the one the bond cannot cover for you.
7. Confirm continuity from the +5 V bus, through each branch fuse, to every one of that run's red leads - **both** of them under Option A.
    Do this per branch; it also confirms each fuse is actually seated, and, under Option A, that neither injection leg has been left off.
8. Confirm **no continuity between the +5 V bus and mains earth**, and none between the GND bus and any mains conductor.
9. Confirm the DATA wire for each channel is on the `+` pad and its ground on the `−` pad of the same channel, and that no data wire is touching a neighbouring pad.
    The pads are 2.54 mm apart and solder bridges there are easy to make and easy to miss.

**Supply alone, no strips connected**

10. Disconnect the +5 V bus from the supply's V+ terminal.
    Plug in and switch on.
11. Meter on DC volts across the supply's V+ and V− terminals.
    You should read close to 5 V, right polarity.
12. Adjust the V-ADJ trimpot until it reads **5.00 V**.
    Not 5.2, not 5.3, for the reason in §2.4.
    If your unit has no adjustment, confirm it reads between 4.90 and 5.10 V and carry on.
13. Switch off and unplug.
    Reconnect the +5 V bus.

**First light, one run**

14. Connect exactly **one** run: its data pair, plus both red and both black leads under Option A, or the single red and black pigtail leads under Option B.
    Leave the other branch fuses out.
15. Power on.
    Meter on DC volts at that run's red and black leads at the strip end.
    With the run dark you should read essentially the supply voltage.
16. Drive that run at full white and meter at the strip pads again.
    With one run lit the feeder and the bus are carrying 3.84 A rather than the zone total, so they contribute about 0.01 V and essentially all of the drop is the branch.
    **The pads should read 4.84 V or better.**
    Below that, the branch legs are too thin or too long: fix it before adding runs, because the feeder has not yet spent its share of the budget and there is nothing to borrow from later.
17. Still at the brightness your option assumes - full white for Option A, your chosen cap for Option B - meter at the run's dimmest point, between the strip's own +5 V and GND rails.
    Under Option A that is the **midpoint** of the run; under Option B it is the **far tip**.
    **Subtract that reading from the pad reading in step 16. The difference is the strip's own drop, and it must be 0.250 V or less.**
    Take the difference rather than the absolute voltage here, and do not just check that point against 4.50 V: with one run lit the feeder and the bus are barely loaded, so the whole strip is sitting about 0.09 V higher than it will once the zone is full, and an absolute reading would pass a strip that will fail later.
    This is the one measurement in this document that settles a number nothing else could establish, and §6.3 explains what to do if the strip is over its 0.250 V.
    Look along the run too: a stretch that is dimmer, or drifting pink or amber while the fed end is still white, is the same finding by eye.

**Adding the rest**

18. Power off, add one more run, power on, check it, repeat.
    Adding them one at a time means that when something is wrong you already know which connection you just made.
19. With all runs on the cluster connected, drive everything white briefly and check the supply is not going into current limit and is not getting hot.
    This is also the only moment the feeder carries the current it was sized for, so meter the whole §6.2 ladder now, at full white, with every run on the cluster lit:
    **supply terminals 5.00 V, bus bars 4.925 V or better, strip pads 4.75 V or better.**
    Those three are conductor readings and they hold at full white under either option.
    Then take the last rung, **each run's dimmest point at 4.50 V or better**, at the brightness that option assumes: full white under Option A, your chosen cap under Option B.
    If step 16 passed and the bus reading does not, the extra is the feeder: it is too long or too thin for the zone's current, not the branch.
    If the bus is fine and a pad is low, that run's branch legs are the problem.
    Each reading isolates one segment, which is the whole point of apportioning the budget rather than checking only the total.
20. Leave it running a scene for an hour and come back and feel the supply, the bus bars, and the branch wires.
    Nothing should be more than mildly warm.
    Anything hot is undersized.

---

## 12. Sources

Hardware facts in §2 were verified against these, not from recollection:

| Fact | Source |
|---|---|
| J2 is a 2×8 header; odd pins GND, even pins data via 68 Ω; no +5 V pin on it | `pcb/fc64x8/fc64x8.sch`, Eagle schematic netlist (nets `GND`, `N$31`-`N$38`, parts `J2`, `R4`-`R11`) |
| Level shifter is an SN74HCT245 running from an AAT3110-5.0 charge pump; `OE` grounded, `DIR` to 5 V | Same schematic, parts `U3`, `U2`, net `N$27` |
| Silkscreen prints channels 0-7; the `−` row is nearest the board edge; pad geometry per channel | `pcb/fc64x8/fc64x8.brd`, Eagle board file, layers 21/22 text and package `2X08` pad coordinates |
| Firmware channel order: strip 1 = PTD0 … strip 8 = PTD7 | `firmware/OctoWS2811z.cpp` and `firmware/core_pins.h` |
| Design intent: level shifter and series termination present because 3.3 V is unreliable with WS2811; boost converter gives the shifter a stable 5 V | Upstream `pcb/README.md` |
| Strip power comes from a separate brick, not the board; `+`/`−` pad convention; edge row is `−`; ground wire paired with the data wire | *LED Art with Fadecandy*, Micah Elizabeth Scott, published by Adafruit |
| WS2812B `VIH` = 0.7 × VDD, `VIL` = 0.3 × VDD, `VDD` characterised 4.5-5.5 V, input current ±1 µA, input capacitance 15 pF, `T0H`/`T1H`/tolerance | WS2812B datasheet (Adafruit-hosted copy) |
| SN74HCT245 `VOH` min 4.40 V at −20 µA and 3.84 V at −6 mA, both at `VCC` 4.5 V | Texas Instruments SN74HCT245 datasheet |
| AAT3110 charge pump delivers up to about 100 mA | Skyworks / AnalogicTech AAT3110 datasheet |
| fcserver device addressing by `serial`; map entry format; device pixel numbering 0-511 with strands at 64-pixel boundaries; warning about running on untrusted networks | Upstream `doc/fc_server_config.md` |
| Conductor ampacity, 14 AWG and larger | NFPA 70 (NEC), Table 310.16, 60 °C copper column |
| Conductor ampacity, 16 AWG | NFPA 70 (NEC), Table 402.5 |
| Why the 60 °C column applies to 105 °C wire | NFPA 70 (NEC), 110.14(C)(1)(a) |
| Small-conductor overcurrent limits, and the distinction between those and ampacity | NFPA 70 (NEC), 240.4(D) |
| Conductor DC resistance, stranded uncoated copper at 75 °C | NFPA 70 (NEC), Chapter 9, Table 8 |
| Standard fuse ratings (1, 3, 6, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60 A) | NFPA 70 (NEC), 240.6(A) |
| Continuous load defined as three hours or more; conductor and overcurrent device sized to 125 % of it | NFPA 70 (NEC), Article 100, 210.19(A)(1)(a) and 210.20(A) |
| Voltage drop guidance: 3 % on a branch circuit, 5 % across feeder and branch together | NFPA 70 (NEC), 210.19(A) Informational Note No. 4 |

Upstream repository files were read from the `PimentNoir/fadecandy` mirror of `scanlime/fadecandy`, because the original `scanlime/fadecandy` repository returned 404 at the time of writing.
The mirror's `pcb/README.md` still links back to `scanlime/fadecandy` paths, and its file contents are consistent with the published board.

Explicitly **not** verified, and flagged where it appears:

- The rail resistance of these specific silicone-sleeved strips (§6.3). This is the one input the §6.2 sizing chain does not close on its own. It changes nothing about what cable or fuses to buy; it decides only how many points a run is fed at and at what brightness, and step 17 of the §11 checklist settles it with a meter.
- The Pi 3B+ total USB port current limit of 1.2 A - widely cited in Raspberry Pi community sources, not confirmed against official documentation here (§8).
- WS2812B per-pixel quiescent current, used only for the standby estimate in §7.
The NEC is used here as a published, current ampacity and voltage-drop standard, because a 5 V lighting installation needs *some* citable basis and "typical chassis-wiring values" is not one.
It is not a claim that this installation is a code-regulated branch circuit or that it has been inspected.
Where the NEC and this document differ, it is stated explicitly: §6.2 sizes conductors to the fuse rather than to the load, and takes the smallest standard fuse at or above 125 % of load rather than any rating in a permitted range.
Both are tighter than the code minimum, deliberately.
