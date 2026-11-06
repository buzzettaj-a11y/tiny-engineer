# Chip add-on electronics audit (modular build)

Fact-check of the planned **Chip** add-ons (INMP441 mic, GP7 mute button or rocker, INA219 servo-current sensor, optional VBUS rocker) against the **original modular Tiny Engineer electronics**: Waveshare ESP32-C3-Zero, Adafruit PCA9685 breakout + 5× SG90, Waveshare 0.91" SSD1306 OLED, MAX98357A breakout + speaker, Adafruit 5993 USB-C breakout, 5 V ≥ 2 A USB supply.

Written 2026-11-05 against the `chip` branch working tree. While this audit was being written, another change added a `mute_switch` setting ([ADR 0004](../adr/0004-mute-switch-open-means-muted.md)). The firmware notes below describe the code as it stood at 21:53 that day.

## Verdict (plain language)

**The add-ons work with the original electronics. Wired as intended, nothing in the plan damages the stock parts.** No I2C address conflicts, no new boot-strapping risk, and GP6/GP7 are good pins. The mic's clocks are inside its limits, and the 3.3 V regulator has plenty of headroom.

What can go wrong is mostly **wiring guidance** and **one firmware default**:

- The INA219 is easy to wire so that it measures only part of the servo current, or none of it. The current docs do not stop you. If that happens, the bump reflex is silently blind.
- A maintained rocker on GP7 only behaves correctly if you turn on the new `mute_switch` setting. Left in the default button mode, the rocker position and the real mute state disagree. That is a privacy failure.
- The default bump threshold (650 mA) may be higher than an SG90 can even draw when it stalls. The reflex might never fire until you measure and tune it.

### Must fix

1. **Make the INA219 the *only* path to PCA9685 V+.** The connection table and power section in [wiring.md](../hardware/wiring.md) still show USB 5 V going straight to the PCA9685 "5V"/V+ rail. The INA219 appears only in one sentence. The Adafruit board also accepts servo power on the V+ pins of **both** 6-pin side headers [15][14]. Rewrite it as VBUS → INA219 VIN+ → VIN− → PCA9685 terminal block, with nothing else on V+.
2. **A rocker on GP7 needs `mute_switch` = `true`.** In the default button mode, every *closing* toggles mute and opening does nothing. Booting with the rocker closed flips the saved state. So "O" can mean listening. Switch mode (open = muted, `POST /mic` refused) is correct and fails safe.
3. **Bridge INA219 A0 before the module touches the bus.** An unbridged INA219 sits at 0x40, the same address as the PCA9685 [12][13]. Both chips then ACK the same traffic, and servo control is corrupted without a clean error. Check with an I2C scan: expect exactly 0x3C, 0x40 and 0x41.
4. **Measure an SG90 stall current and set `bump_threshold_ma` from it.** One vendor measures an SG90 stall at 360 mA [23]. That is below the default 650 mA threshold above baseline, and even below the 400 mA explore cap. Until it is tuned, the reflex may only fire on multi-joint collisions.

### Should fix

5. Return servo ground through the PCA9685 **terminal-block GND**, paired with V+, using ≥ 22 AWG wire. Do not return it through the header GND pin that I2C shares.
6. 5 V supply: 2 A covers normal motion. It does not cover peaks: all five servos moving plus audio plus Wi-Fi is about 2.3 A, and stalls are more. Prefer a 5 V / 3 A USB-C source. Add a ~3 A polyfuse in VBUS; the main control board has one and the modular harness has none.
7. VBUS rocker: use one rated ≥ 6 A with ≤ 50 mΩ contact resistance [25], in **VBUS only** (never GND), before the split. Remember that the C3-Zero's onboard USB-C ties straight to its 5V pad [7]. With the rocker off, that port back-feeds the whole rail, servos included.
8. Mute rocker: add an external 4.7–10 kΩ pull-up on GP7. The internal ~45 kΩ gives only ~73 µA of contact wetting current [1], which suits a mains-rated rocker poorly. An optional 100 nF to GND also helps.
9. Shared I2S clocks: keep BCLK/WS short. If the mic sits more than ~20 cm away (head or neck), add 33–100 Ω series resistors on the mic's SCK/WS branch. Solder or latch the mic's VDD/GND: clocking an unpowered INMP441 is outside its ratings [9].
10. The full-duplex RX channel runs as an internal I2S slave with MCLK/BCLK = 4. That is exactly ESP-IDF's measured minimum [4]. Consider `I2S_MCLK_MULTIPLE_512` for margin.
11. The MAX98357A datasheet says 22.05 kHz is **not supported** [17]. Stock firmware already ran there, so this predates Chip. Verify on the bench, and consider running the bus at 44.1 kHz; the mic stays in spec.
12. Doc and comment corrections (details in [§9](#9-wiring-guidance-errors-and-risks)):
    - the I2C speed: OLED bursts run at 400 kHz;
    - the 5993 "asks for 1.5 A" wording;
    - the ina219.cpp "5 V brownout" comment;
    - a stray table row in interfaces.md;
    - MAX98357A SD-pin channel claim.
13. Optional: a 10 kΩ pull-up from GP2 to 3V3. Espressif recommends it for GPIO2 because of glitches [1].

### Fine

- I2C address map: 0x3C OLED, 0x40 PCA9685, 0x41 INA219. The PCA9685 ALLCALL address (0x70) is live only until `pwm.begin()` clears it, and the sub-addresses are off [13][16].
- Combined pull-ups are about 2.3–3.1 kΩ (1.1–1.4 mA sink), well inside the 3 mA I2C limit. Rise time is OK at 400 kHz for short wiring.
- Strapping pins (GPIO2/8/9): the Chip connections add no pull to them. GPIO8 and GPIO9 are untouched.
- GP6 and GP7 are plain GPIO in this configuration: not strapping, flash or USB pins, and JTAG is on USB by default [1][2].
- INMP441 clocks are inside its limits: SCK 1.411 MHz (0.5–3.2 MHz), WS 22.05 kHz (7.8–50 kHz), 64 SCK per frame, L/R=GND = left slot, SD pull-down present [9].
- The MAX98357A accepts 64 BCLK per LRCLK. Its BCLK/LRCLK/DIN pins tolerate 6 V even when unpowered [17].
- The 3.3 V regulator peaks at about 375 mA against an 800 mA ME6217 [8].
- INA219: common-mode range 0–26 V independent of VS [11]. PGA /8 gives ±3.2 A, about 0.32 V drop at full scale. Measuring only the servo lead is the right choice. Its inputs are safe unpowered [11].
- `mute_switch` mode is level-following and fail-safe (open or broken wire = muted).

---

## 1. I2C bus: addresses, pull-ups, capacitance

**Addresses.** Firmware uses PCA9685 `0x40`, INA219 `0x41` and OLED `0x3C` ([`include/pins.h`](../../include/pins.h)).

| Device | Address(es) it answers | Source |
| --- | --- | --- |
| SSD1306 OLED | 0x3C (SA0 low) or 0x3D (SA0 high) | SSD1306 module manual quoting the controller [19] |
| PCA9685 | 0x40 (A0–A5 low); **LED All Call 0x70, enabled at power-up** (MODE1 ALLCALL default 1); SUBADR1–3 = 0x71/0x72/0x74, **disabled** at power-up; SWRST through General Call 0x00 | NXP PCA9685 Rev 4 [13] |
| INA219 | 0x40–0x4F set by A1/A0. A1=GND, A0=VS → 0x41 | TI SBOS448G [11]; Adafruit: A0 bridged = 0x41, A1 = 0x44, both = 0x45 [12] |

- `pwm.begin()` in [`servo_wrapper.cpp`](../../src/hardware/servo_wrapper.cpp) calls the Adafruit library's `reset()`. That writes `MODE1 = MODE1_RESTART` as the whole byte, so ALLCALL is cleared. `setPWMFreq()` keeps the other bits [16]. So 0x70 is live only from PCA9685 power-up until `initPca9685()`, which runs right after `Wire.begin()` ([`main.cpp`](../../src/main.cpp)). Nothing in the Chip build uses 0x70–0x74, so **no conflict**. For future add-ons: avoid 0x70, the default address of common I2C muxes and LED drivers, because it is ALLCALL until boot reaches `begin()`.
- **Risk:** INA219 modules ship with A0/A1 open, which is **0x40**. If the jumper is forgotten, the INA219 and PCA9685 share an address. Both ACK, and writes to PCA9685 register 0x00 (MODE1) also land in INA219 register 0x00 (config). Firmware does not catch this: the 0x40 probe passes, and the 0x41 probe just logs "INA219 not found". → **Must fix #3**: bridge first, then scan.

**Pull-ups.**

- Adafruit PCA9685: 10 kΩ on SDA and SCL, to its VCC (3.3 V here) [14].
- Adafruit INA219: 10 kΩ on SDA and SCL [12]. Its VIN pin should match MCU logic, so 3.3 V [12].
- Waveshare 0.91" OLED: no pull-up values published, as far as I could find [20]. Assume 4.7 kΩ, 10 kΩ or none.
- ESP32-C3 internal pull-up: ~45 kΩ [1], enabled by Arduino-ESP32's I2C HAL (`enable_internal_pullup = 1` in `esp32-hal-i2c-ng.c`) [5].

| OLED pull-ups | Effective Rp (per line) | Sink current when low (3.3 V) |
| --- | --- | --- |
| 10 kΩ | 10 ∥ 10 ∥ 10 ∥ 45 kΩ ≈ **3.1 kΩ** | ≈ 1.1 mA |
| 4.7 kΩ | 10 ∥ 10 ∥ 4.7 ∥ 45 kΩ ≈ **2.3 kΩ** | ≈ 1.4 mA |

The I2C-bus figures used here: VOL ≤ 0.4 V at 3 mA, so Rp(min) = (3.3 − 0.4) V / 3 mA ≈ 0.97 kΩ; rise time ≤ 1000 ns (Standard mode) and ≤ 300 ns (Fast mode); Rp(max) = tr / (0.8473 · Cb). These are standard figures from UM10204; I could not load the spec in this session (see the note under Sources). Both cases sink well under 3 mA and **pass**. Adding the INA219's 10 kΩ was harmless.

**Bus speed and capacitance.**

- [interfaces.md](../hardware/interfaces.md) says the bus runs at the Arduino default of 100 kHz. The HAL does default to 100 kHz when no frequency is given [5].
- But the OLED object is constructed with the library defaults `clkDuring = 400000UL` and `clkAfter = 100000UL` [18]. So every OLED frame push runs at **400 kHz Fast mode**, and that sets the rise-time budget.
- At Rp ≈ 3.1 kΩ, Fast mode allows Cb ≤ 300 ns / (0.8473 × 3.1 kΩ) ≈ **114 pF**. At 2.3 kΩ the limit is ≈ 154 pF.
- Four device pins plus the ESP32 add roughly 40–50 pF; the I2C spec allows up to 10 pF per pin. That leaves a few tens of pF for wire. Keep the whole SDA/SCL harness short (≲ 50 cm total).
- INA219 and PCA9685 traffic at 100 kHz has ~3× more margin. The INA219 supports up to 400 kHz in Fast mode [11]; the PCA9685 goes up to 1 MHz [13].

## 2. Strapping pins and boot

ESP32-C3 datasheet v2.4 [1]:

- Strapping pins are **GPIO2, GPIO8, GPIO9**.
- At reset, GPIO2 and GPIO8 **float (no default pull)**. GPIO9 has a weak pull-up.
- SPI boot needs GPIO9 = 1. Joint download needs GPIO2 = 1, GPIO8 = 1, GPIO9 = 0.
- Footnote: GPIO2 does not actually decide between SPI boot and download boot, but "it is recommended to pull this pin up due to glitches".
- Straps are latched within a 3 ms hold window after CHIP_EN rises.

The C3-Zero schematic shows no resistor on IO2, IO8 or IO9 apart from the BOOT button on IO9 [7].

| Pin | Chip connection | Effect at reset / flash |
| --- | --- | --- |
| GPIO2 | MAX98357A BCLK (stock) + **INMP441 SCK (new)** | Both are high-impedance CMOS inputs. The MAX98357A has ±1 µA leakage and no pull on BCLK [17]. The INMP441 SCK pin has no pull in the datasheet [9]. The module reportedly carries only a filter cap and two pull-down resistors, almost certainly on SD and L/R [10]. **No change to the strap.** Worth checking: meter INMP441 SCK to GND on the module; it should read open. Optional: a 10 kΩ pull-up to 3V3 on GP2, per Espressif's note (Should fix #13). |
| GPIO8 | none | Untouched |
| GPIO9 | none | Untouched (BOOT) |
| GPIO6 | INMP441 SD (mic output) | Not strapping. Has a weak pull-up at reset while `EFUSE_DIS_PAD_JTAG` = 0 [1]. The mic is not clocked yet, so SD is tri-stated: harmless. Power-up glitch is a ~5 ns low pulse [1]: harmless into a tri-stated mic output. |
| GPIO7 | mute button / rocker to GND | Not strapping. Input at reset, ~5 ns power-up glitch [1]. Holding the button, or booting with the rocker closed, cannot change the boot mode. |

**Verdict:** no new boot or flash risk.

## 3. GP6 and GP7 suitability

- GPIO4–7 are the JTAG pads (MTMS, MTDI, MTCK = GPIO6, MTDO = GPIO7) [1][2]. "By default, ESP32-C3 JTAG interface is connected to the built-in USB_SERIAL_JTAG peripheral" [2]. The pads become JTAG only if the `DIS_USB_JTAG` eFuse is burned. Never do that on this robot: it would also take GP4 (amp DIN) and GP5 (PCA9685 OE).
- GPIO12–17 are the in-package flash pins, and GPIO18/19 are USB [1]. So GP6 and GP7 are free general-purpose pins, as [pinout.md](../hardware/pinout.md) says.
- GP6 as I2S data in: `initI2sBus()` turns off the pull-up and turns on the ~45 kΩ internal pull-down [1] after `I2S.begin()` ([`audio.cpp`](../../src/audio/audio.cpp)). The datasheet asks for a 100 kΩ pull-down to discharge SD while it is tri-stated [9]. 45 kΩ is stronger but fine: the mic drives SD actively in its own slot.
- GP7: `INPUT_PULLUP`, about 73 µA when closed (3.3 V / 45 kΩ). It is configured only in `initMic()`, which runs after Wi-Fi and I2S bring-up, and the button task starts there too. Before that the pin floats harmlessly.

## 4. INMP441 sharing BCLK/WS with the MAX98357A

**Clock limits.** Bus: `I2S_MODE_STD` (Philips), 22050 Hz, 32-bit data, stereo ([`audio.cpp`](../../src/audio/audio.cpp)). That gives BCLK = 22050 × 2 × 32 = **1.4112 MHz** and 64 BCLK per frame.

| Parameter | Bus | INMP441 limit [9] | MAX98357A limit [17] |
| --- | --- | --- | --- |
| SCK/BCLK | 1.4112 MHz (708.6 ns period) | 0.5–3.2 MHz, tSCP ≥ 312 ns, high and low ≥ 50 ns | 0.2432–25.8 MHz, high and low ≥ 15 ns |
| BCLK per frame | 64 | 64 (32 per channel, 24-bit word) | 32, 48 or 64 × LRCLK |
| WS/LRCLK | 22.05 kHz | 7.8–50 kHz | **8, 16, 32, 44.1, 48, 88.2, 96 kHz only; "22.05kHz … NOT supported"** |

- The mic is fully in spec.
- The amp's 22.05 kHz limit was already true of the stock 16-bit build, so it is not a Chip regression. Analog Devices states it twice, and the LRCLK ranges jump from 15.2–16.8 kHz straight to 30.4–50.4 kHz [17]. What the amp does at an unsupported rate is not specified.
- If the stock robot's audio has worked on real hardware, this is evidently tolerated by the parts in use. It is still out of spec. **Should fix #11**:
  - Option: run the bus at **44.1 kHz**. INMP441 SCK becomes 2.822 MHz, still under 3.2 MHz, and WS 44.1 kHz, still under 50 kHz.
  - The original assets are 44.1 kHz anyway; `audio_pack.py` halves them.
  - For the speaker, duplicate samples. For the mic, low-pass and decimate 2:1 to keep the 22050 Hz UDP format.

**Fan-out and wiring.**

- One ESP32 pad drives two inputs: MAX98357A at 3 pF [17] plus the INMP441. The ESP32-C3 can source ~40 mA at its strongest drive setting [1]. DC load is not an issue.
- The real risk is **ringing on long unterminated BCLK/WS wires**. A double-clocked edge corrupts frames: clicks on the speaker, misaligned mic words.
- This matters because the mic is likely to sit away from the desk electronics, possibly routed alongside servo leads.
- **Should fix #9**:
  - Keep BCLK/WS short, and twist each with a ground.
  - If the mic branch is long, add 33–100 Ω in series at the driving end of the mic's SCK and WS branches.
  - Or lower the GPIO drive strength.

**Data format and slots.**

- The INMP441 sends 24-bit two's complement, MSB first, one SCK after the WS edge (Philips). It tri-states SD when it is not its channel. L/R low = left channel [9]. That matches `I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG` [5] and `micConvertLeftSlots()` reading `stereo[i*2]` ([`mic_protocol.h`](../../src/mic/mic_protocol.h)).
- The amp is a mono part fed identical left and right samples. Whatever its SD pin selects (left, right or (L+R)/2 [17]) gives the same audio.

**Full-duplex timing (RX as internal slave).**

- Arduino-ESP32 3.3.x `I2SClass::begin()` creates TX and RX in one `i2s_new_channel()` call, both as `I2S_ROLE_MASTER` with `auto_clear = true`, and initialises TX first [5].
- ESP-IDF v5.5 then switches the later channel (RX) to an internal **full-duplex slave** that loops back the on-chip BCLK/WS [4]. ESP-IDF docs: "only one channel can work as the master" [3].
- In that mode the driver keeps MCLK = 256 × fs, so the RX MCLK/BCLK ratio = 256 / 64 = **4**. The driver's own comment: "Measured minimums: TX slave >= 6, RX slave >= 4" [4]. We are exactly at the minimum: no warning, but no margin either.
- Sampling margin itself is large. SD is valid for about half of a 708 ns BCLK period, against ≥ 50 ns high/low limits [9]. So this is a "should": **Should fix #10**. If mic data ever looks bit-shifted or noisy, reconfigure both channels' clocks with `I2S_MCLK_MULTIPLE_512` (`i2s_channel_disable` + `i2s_channel_reconfig_std_clock`). MCLK settings are not a full-duplex pairing condition [3].
- `auto_clear = true` makes TX send zeros when no audio is queued, so BCLK/WS never stop [5]. The mic never drops into standby: stopping SCK puts it in standby, and it takes 2^14 SCK cycles to resume [9].
- After power-up the mic outputs zeros for 2^18 SCK cycles, ≈ **186 ms** at 1.4112 MHz [9]. That is well inside boot time.

## 5. 3.3 V budget (C3-Zero LDO)

- C3-Zero regulator: **ME6217C33M5G**, VIN on the 5V net [7]. Rated 800 mA when VIN ≥ VOUT + 1 V, with overcurrent and thermal protection [8].
- The onboard WS2812 is on the **5V** net, not 3V3 [7].

| Load on 3V3 | Peak | Source |
| --- | --- | --- |
| ESP32-C3, Wi-Fi TX (802.11b, 21 dBm) | 335 mA | [1] |
| SSD1306 0.91" OLED | ~10–25 mA (estimate; Waveshare publishes no figure [20]) | — |
| PCA9685 logic | 6 mA typ / 10 mA max | [13] |
| INMP441 | 2.2 mA typ / 2.5 mA max | [9] |
| INA219 | 0.7 mA typ / 1 mA max | [11] |
| I2C pull-ups (lines low) | ≤ 1.5 mA | §1 |
| GP7 pull-up (closed) | 0.07 mA | §3 |
| **Total peak** | **≈ 375 mA** | < 800 mA |

- Average draw is far lower; Wi-Fi peaks are bursts.
- Dissipation: at a 150 mA average, (5.0 − 3.3) V × 0.15 A ≈ 0.26 W in SOT-23-5. Acceptable.
- Dropout headroom: LCSC quotes 100 mV at 300 mA for the series [8]. VBUS can sag to roughly 3.5 V before 3V3 droops. **Fine.**

## 6. 5 V budget

| Load on +5V | Typical | Peak / worst | Source |
| --- | --- | --- | --- |
| C3-Zero (linear LDO: input current ≈ output current) | ~0.1 A | ~0.38 A | §5 |
| WS2812 (5V net) | small | ≲ 0.06 A (full white) | [7] |
| MAX98357A + 8 Ω speaker | 2.75 mA quiescent | 1.4 W at 1% THD into 8 Ω at 92% efficiency ≈ 0.3 A average at full sine; instantaneous ≈ 0.6 A | [17] |
| SG90 × 5: idle | 5 × 10 mA | — | [23] |
| SG90 × 5: moving | 0.1–0.25 A each | all five: 0.5–1.25 A | [23] |
| SG90 × 5: stall | — | 360 mA each measured → **1.8 A**; often-quoted figures of ~0.65–0.75 A each → **3.3–3.8 A** | [23]; no SG90 current figure in the datasheet [24] |

- **Typical busy robot** (2–3 servos moving, moderate audio): ≈ 0.7–1.2 A.
- **All five moving + loud audio + Wi-Fi burst:** ≈ 2.3 A peak.
- **Stalls:** 2.4–4.5 A.
- So a 5 V / 2 A source runs normal motion but can sag on transients. A stall the reflex does not catch will brown out the rail before anything else happens.
- The 2500 mA absolute bump trip ([api.md](../api.md#bump-reflex)) is unreachable on a 2 A supply. The relative threshold is what protects the robot (see §7).
- **Should fix #6:** a 5 V / 3 A USB-C source. A C-to-C cable from a 3 A source advertises 3 A over CC (§9). Add a ~3 A polyfuse in VBUS: the main control board has a 3 A PPTC and an eFuse ([power.md](../hardware/power.md)); the modular harness has nothing.

## 7. INA219: range, drop, placement, threshold

**Configuration** ([`ina219_regs.h`](../../src/hardware/ina219_regs.h)):

- `INA219_CONFIG_VALUE` = BRNG 0, PG /8, BADC 12-bit, SADC `1011` (12-bit × 8 averages, 4.26 ms), MODE `101` (shunt, continuous).
- The bit layout and the 4.26 ms figure match the datasheet [11].
- Calibration 4096 = trunc(0.04096 / (0.1 mA × 0.1 Ω)): correct formula. It does not matter much, since firmware reads the shunt register.
- After a power-on reset the INA219 defaults to config 0x399F, which is also PG /8 [11]. So reads stay on the same scale even before the firmware rewrites the config.

**Range and resolution.**

- PGA /8 = ±320 mV → **±3.2 A** with 0.1 Ω [11].
- The shunt register LSB is 10 µV = 0.1 mA.
- Offset at /8: ±40 µV typ, ±200 µV max for INA219A, i.e. ±0.4 mA typ, ±2 mA max [11].
- My inference, not stated in the datasheet text I could extract: at /8 the 12-bit converter's real step is coarser than the 10 µV register LSB, roughly 80 µV ≈ 0.8 mA. The ×8 averaging smooths that.
- Either way, resolution is two to three orders of magnitude finer than the bump threshold.
- /8 is the right setting. /4 (±1.6 A) would clip during multi-servo stalls.

**Common-mode.**

- VIN+ sits at ~5 V; the input common-mode range is 0–26 V, independent of the 3.3 V supply.
- "The inputs can withstand the full 0 V to 26 V range … even when the device has no power applied", and there is no power-sequencing requirement [11].
- **Fine.**

**Shunt drop and heat.**

| Servo current | 0.1 Ω drop | Shunt power |
| --- | --- | --- |
| 0.65 A (around one trip) | 65 mV | 0.04 W |
| 1.8 A (five measured stalls) | 0.18 V | 0.32 W |
| 3.2 A (full scale) | 0.32 V | 1.0 W |

- Add wire, screw terminals, and the PCA9685 board's reverse-polarity element in its terminal-block input [15]. At a heavy stall the servos see roughly 4.5–4.7 V. The SG90 is specified at 4.8 V nominal [24], 4.8–6 V [23].
- Acceptable, since stalls are short-lived once the reflex works.
- Adafruit does not state the shunt's power rating [12]. Check the resistor package on a generic module if you expect sustained > 2 A.
- Fit a 470–1000 µF bulk capacitor in the Adafruit board's V+ capacitor spot [15], on the load side of the shunt. It supplies servo current spikes locally and smooths what the INA219 sees.

**Placement: servo lead only. This is right.**

- The INA219 must not see the amp (≈ 0.6 A audio peaks would look like bumps), Wi-Fi bursts or the LED. Only the servo lead gives the reflex a clean signal.
- High-side sensing: VIN+ to the supply, VIN− to the load [12]. That matches [interfaces.md](../hardware/interfaces.md).
- The PCA9685 chip itself runs from VCC (3.3 V), not V+ [14]. So the V+ lead carries only servo current.
- But see **Must fix #1** (§9): the rail must have *one* entry point.

**Threshold vs SG90 (Must fix #4).**

- The reflex trips at `baseline + bump_threshold_ma` (default **650 mA**) for 60 ms while moving ([api.md](../api.md#bump-reflex), [`settings.h`](../../src/settings/settings.h)). Explore uses `min(threshold, 400)`.
- The SG90 datasheet gives **no current figures** [24]. ProtoSupplies measures 10 mA idle, 100–250 mA moving and **360 mA stall** at 5 V [23]. Figures of 650 mA and up are widely repeated but are for other or unspecified micro servos; I could not verify one from a primary source.
- With a 360 mA stall, one jammed SG90 rises perhaps 250–350 mA above the baseline. That is **below both** the 650 mA default and the 400 mA explore cap.
- Bench procedure:
  1. Read `current_ma` from `GET /ranges` while holding one horn.
  2. Set `bump_threshold_ma` to roughly 60–70% of (stall − baseline). Likely 150–250 mA for SG90.
  3. Confirm that ordinary multi-joint moves do not false-trip.

## 8. Back-powering when a part loses power

| Part loses its supply wire | What is still driving it | Datasheet limit | Outcome |
| --- | --- | --- | --- |
| INMP441 VDD | ESP32 drives SCK/WS at 3.3 V | Digital inputs −0.3 V to VDD + 0.3 V; the datasheet advises not to apply WS/SCK while VDD is off [9] | ESD diodes phantom-power the mic from the clock lines. Possible damage or latch-up. Low likelihood (same 3V3 rail), so the risk is a loose jumper. Secure VDD/GND. Series resistors (Should fix #9) limit the current. |
| INA219 VCC | I2C pull-ups from the other modules | SDA: up to 6 V regardless of VS; **SCL: VS + 0.3 V** (clamp); 5 mA per pin [11] | The SCL clamp plus the module's own 10 kΩ pull-ups into a dead VCC drag both lines toward 0 V. **The whole I2C bus can stop working, PCA9685 servo control included**, not just the current reading. Same pattern if the OLED's VCC is lost. Keep these connections positive-locking or soldered. |
| MAX98357A VIN | ESP32 drives BCLK/LRC/DIN | BCLK/LRCLK/DIN −0.3 V to +6 V vs GND, independent of VDD [17] | Safe |
| INA219 with 5 V present, VS off | VIN± at 5 V | Inputs tolerate 0–26 V unpowered [11] | Safe |

Everything else shares the same 5 V and 3.3 V rails and loses power together. The VBUS rocker cuts everything at once, so it creates no partial-power state. The exception is the C3-Zero onboard USB port; see §9 item 7.

## 9. Wiring guidance errors and risks

1. **INA219 bypass (Must fix #1).**
   - [wiring.md](../hardware/wiring.md) "Connection table (as drawn)" still lists `USB 5V/VBUS → PCA9685 5V`, and "Power (as drawn)" still shows `USB 5V → PCA9685 5V`. The INA219 appears only in one sentence under "Pads drawn with no wires".
   - A builder who keeps the table row and adds the INA219 "in series with the 5 V lead" ends up with **two parallel paths**. The INA219 then reads a fraction of the current, or nothing.
   - Adafruit also allows servo power on the side-header V+ pins instead of the terminal block, and "Both sides of the pins are identical!" [14][15]. A 6-pin Dupont housing with V+ populated is a second, unmeasured, non-reverse-protected entry.
   - The OLED is wired to the PCA9685's output-side header, which has V+ right next to VCC. An offset housing would put 5 V on the OLED. If the OLED's pull-ups go to its VCC, that also puts 5 V on the bus, and ESP32-C3 inputs are rated only to VDD + 0.3 V [1].
   - The firmware's plausibility check (holding current < 10 mA) exists only in `/ranges/explore` ([api.md](../api.md#post-rangesexplore)).
   - **Fix:** change the table and power sections to VBUS → INA219 VIN+; INA219 VIN− → PCA9685 terminal-block V+. Add rows for INA219 VCC (3V3), GND, SDA and SCL, the INMP441 pins, and GP7. State that both side-header V+ pins stay unconnected.
2. **Servo ground return (Should fix #5).** The table returns all module grounds through the "PCA9685 GND (logic/header side)". If V+ enters at the terminal block, servo return current flows through a single header pin and thin Dupont wire that the I2C/logic reference shares, causing ground bounce on the bus. Run GND to the terminal block next to V+, ≥ 22 AWG.
3. **No overcurrent protection on the modular path (Should fix #6).** See §6.
4. **I2C speed statement.** [interfaces.md](../hardware/interfaces.md) says the bus runs at "typically 100 kHz". OLED transfers actually run at 400 kHz, the Adafruit_SSD1306 default `clkDuring` [18]. That matters for the capacitance budget in §1.
5. **5993 current wording.**
   - [wiring.md](../hardware/wiring.md) says the two 5.1 kΩ CC resistors "ask the upstream port for 5 V / up to ~1.5 A". The phrase follows Adafruit's product copy [21].
   - In USB Type-C, a 5.1 kΩ Rd only identifies a sink. The **source** advertises Default, 1.5 A or 3 A through its Rp, and the sink may draw up to that [22].
   - So a 3 A Type-C source lets this board draw 3 A. A USB-A charger with an A-to-C cable advertises "Default" but often delivers its rated 2 A anyway.
   - [power.md](../hardware/power.md) already says it right: "The charger decides how much current arrives".
6. **ina219.cpp comment.** "A brownout on the 5 V rail resets the INA219" ([`ina219.cpp`](../../src/hardware/ina219.cpp)). The INA219 is powered from 3V3, and its power-on reset threshold is 2 V [11]. A 5 V-only sag does not reset it, unless 3V3 collapses too, which would usually reset the ESP32 as well. The once-a-second config check is harmless; only the comment and the log text are misleading.
7. **VBUS rocker (Should fix #7).**
   - Placement: right after the 5993, in VBUS, before the split to the ESP32 5V pad, the INA219 and the amp. That is correct, and it must be the high side: switching GND would leave grounds tied through D+/D− and the shields.
   - Rating: a common KCD1 rocker is rated 6 A / 250 VAC and 10 A / 125 VAC, with no DC rating and **≤ 50 mΩ** contact resistance [25]. 5 V DC at ≤ 4 A is easy for those contacts. 50 mΩ costs up to 0.15–0.2 V at peak current, on top of the shunt drop. Avoid the "3 A 250 VAC" mini variants, and illuminated mains types (neon lamp).
   - Back-feed: the C3-Zero schematic shows **no diode** between its USB VBUS and the 5V pad [7]. With the rocker off, plugging a computer into the onboard USB-C powers the whole downstream rail from that port: servos, amp and INA219. With the rocker on, two 5 V sources fight. Keep the onboard port unused once the harness is built, as [wiring.md](../hardware/wiring.md) already says.
8. **Mute input wording.** Current docs ([pinout.md](../hardware/pinout.md), [interfaces.md](../hardware/interfaces.md), [api.md](../api.md#post-mic)) now describe both modes correctly. One stray table row, "ESP32 onboard USB-C | — | Unused…", is appended to the mute-input table at the end of [interfaces.md](../hardware/interfaces.md); it belongs in the USB section.
9. **MAX98357A SD/GAIN note.** [components.md](../hardware/components.md) and [interfaces.md](../hardware/interfaces.md) say the breakout's SD is "pulled up ≈ left channel". What the SD pin selects depends on the breakout's resistor to Vin against the chip's internal 100 kΩ pull-down: left, right or (L+R)/2 [17]. The firmware sends identical L/R samples, so it does not matter. The claim is unverified for the AliExpress breakout. GAIN floating = 9 dB is correct [17].

## 10. Maintained I/O rocker on GP7: what actually happens

**Button mode, the default** (`mute_switch` = `false`). Code: `MicButton::update()` in [`mic_protocol.cpp`](../../src/mic/mic_protocol.cpp), `buttonTask`/`applyButtonPresses` in [`mic.cpp`](../../src/mic/mic.cpp).

- `update()` returns `true` only when the debounced level changes to *closed*. Opening returns `false`. Each `true` toggles the saved mute in NVS.
- `stable_` starts `false` at boot. A rocker that is **closed at boot** is debounced 30 ms after the button task starts and **counts as a press**: the saved state flips.

| Situation | Result |
| --- | --- |
| Saved unmuted, boot with rocker closed ("I") | Becomes **muted** |
| … then flip to open ("O") | Stays **muted** (opening does nothing) |
| … flip to closed ("I") again | **Unmuted** |
| Saved muted, boot with rocker closed | Becomes **unmuted** while the rocker is in the same position as above |
| `POST /mic` | Overrides freely; rocker position no longer means anything |

The position-to-state mapping inverts unpredictably, and the robot can stream while the rocker sits in the position the user believes is "mute". **This must not ship with a rocker.**

**Switch mode** (`mute_switch` = `true`, [ADR 0004](../adr/0004-mute-switch-open-means-muted.md)):

- `micEffectiveMutedMode()` returns `!switchClosed`. The debounced level is published every 5 ms by the button task (`g_switchClosed`).
- The mic counts as muted until the switch has read closed for 30 ms after boot.
- Flips are not toggles and do not write NVS.
- `POST /mic` answers 409 ([api.md](../api.md#post-mic)).
- A broken or unplugged wire reads open, so it fails safe to muted.

This is the correct behaviour for a maintained rocker. Hardware notes:

- Use a plain 2-pin SPST rocker.
- Add a 4.7–10 kΩ external pull-up to 3V3 (Should fix #8). Mains-rated silver contacts switching ~73 µA "dry" can become intermittent; in switch mode that fails safe to muted, but it is annoying.
- Do not enable switch mode with nothing on GP7, or the robot stays muted ([api.md](../api.md#post-mic)).

## Sources

All accessed 2026-11-05.

1. Espressif, *ESP32-C3 Series Datasheet* v2.4: strapping pins and boot-mode table (§3), strapping timing (Table 3-2), IO MUX reset states (Table 2-1), power-up glitches (Table 2-2), RPU/RPD and VIH/VIL (Table 5-4), Wi-Fi TX current (Table 5-7). https://documentation.espressif.com/esp32-c3_datasheet_en.pdf (redirect from https://www.espressif.com/sites/default/files/documentation/esp32-c3_datasheet_en.pdf)
2. Espressif, ESP-IDF (ESP32-C3), *Configure Other JTAG Interfaces*. https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-guides/jtag-debugging/configure-other-jtag.html
3. Espressif, ESP-IDF (ESP32-C3), *I2S* driver: full-duplex, master/slave, MCLK. https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/peripherals/i2s.html
4. Espressif, ESP-IDF `release/v5.5`, `components/esp_driver_i2s/i2s_std.c` (`i2s_std_calculate_clock`, full-duplex slave minimum ratio) and `i2s_common.c` (master-to-slave fallback). Read from a local checkout at commit 3ea97e7. https://github.com/espressif/esp-idf/blob/release/v5.5/components/esp_driver_i2s/i2s_std.c
5. Espressif, arduino-esp32: `libraries/ESP_I2S/src/ESP_I2S.cpp` (channel config, `auto_clear`, init order) and `cores/esp32/esp32-hal-i2c-ng.c` (100 kHz default, internal pull-up). Read from a local checkout at commit 286848c; this repo's platform pins core 3.3.12. https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP_I2S/src/ESP_I2S.cpp
6. Waveshare, *ESP32-C3-Zero* wiki. https://www.waveshare.com/wiki/ESP32-C3-Zero
7. Waveshare, *ESP32-C3-Zero schematic V2*: ME6217C33M5G on the 5V net, WS2812 on 5V/IO10, no VBUS diode, no strap resistors. https://files.waveshare.com/wiki/ESP32-C3-Zero/ESP32-C3-Zero-Sch-V2.pdf
8. LCSC, *Microne ME6217C33M5G* (800 mA, protection, dropout). https://www.lcsc.com/product-detail/C427602.html
9. TDK InvenSense, *INMP441 datasheet* DS-INMP441-00 Rev 1.1 (Digi-Key HTML copy). https://www.digikey.cn/htmldatasheets/production/1431884/0/0/1/inmp441-datasheet.html
10. Components101, *INMP441 MEMS Omnidirectional Microphone* (module parts). https://components101.com/modules/inmp441-mems-omnidirectional-microphone
11. Texas Instruments, *INA219 datasheet* SBOS448G (Dec 2015). https://www.ti.com/lit/ds/symlink/ina219.pdf
12. Adafruit, *INA219 Current Sensor Breakout*: pinouts (10 kΩ pull-ups, A0/A1 addresses, VIN±) and overview. https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout/pinouts · https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout/overview
13. NXP, *PCA9685 datasheet* Rev 4 (16 Apr 2015). https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf
14. Adafruit, *16-Channel PWM/Servo Driver*: pinouts. https://learn.adafruit.com/16-channel-pwm-servo-driver/pinouts
15. Adafruit, product 815 page (reverse-polarity protection on the terminal block, side power breakouts, V+ capacitor spot). https://www.adafruit.com/product/815
16. Adafruit, *Adafruit-PWM-Servo-Driver-Library*, `Adafruit_PWMServoDriver.cpp` (`reset()`, `setPWMFreq()`). https://github.com/adafruit/Adafruit-PWM-Servo-Driver-Library/blob/master/Adafruit_PWMServoDriver.cpp
17. Analog Devices, *MAX98357A/MAX98357B datasheet* Rev 16. https://www.analog.com/media/en/technical-documentation/data-sheets/MAX98357A-MAX98357B.pdf
18. Adafruit, *Adafruit_SSD1306*, `Adafruit_SSD1306.h` (I2C constructor defaults). https://github.com/adafruit/Adafruit_SSD1306/blob/master/Adafruit_SSD1306.h
19. 0.96" SSD1306 OLED module user manual Rev 2.4.1 (quotes the SSD1306 slave addresses). https://static.chipdip.ru/lib/490/DOC001490735.pdf
20. Welectron, *Waveshare 14657 0.91inch OLED Module*. https://welectron.com/Waveshare-14657-091inch-OLED-Module
21. Adafruit, product 5993, *USB Type C Vertical Breakout - Downstream Connection*. https://www.adafruit.com/product/5993
22. DigiKey TechForum, *Simple way to use USB Type-C to get 5V at up to 3A (15W)* (Rd/Rp current advertisement). https://forum.digikey.com/t/simple-way-to-use-usb-type-c-to-get-5v-at-up-to-3a-15w/7016/11
23. ProtoSupplies, *Servo Motor Micro SG90* (measured idle, moving and stall current). https://protosupplies.com/product/servo-motor-micro-sg90/
24. *SG90 9 g Micro Servo* datasheet copy (Uni Hamburg course material; no current figures). https://tams.informatik.uni-hamburg.de/lehre/2023ss/vorlesung/es/doc/sg90.pdf
25. Yueqing Daier, *KCD1-102-1 rocker switch* datasheet (6 A/250 VAC, 10 A/125 VAC, ≤ 50 mΩ). https://www.rcscomponents.kiev.ua/datasheets/KCD1-1-102-1.pdf
26. Texas Instruments, *SLVA689 I2C Bus Pullup Resistor Calculation* (only the table of contents loaded). https://www.ti.com/document-viewer/lit/html/SLVA689/pullup-resistor-calculation-slva6899380.html

Repo files read: [`include/pins.h`](../../include/pins.h), [`src/main.cpp`](../../src/main.cpp), [`src/audio/audio.cpp`](../../src/audio/audio.cpp), [`src/mic/mic.cpp`](../../src/mic/mic.cpp), [`src/mic/mic_protocol.h`](../../src/mic/mic_protocol.h)/[`.cpp`](../../src/mic/mic_protocol.cpp), [`src/hardware/ina219.cpp`](../../src/hardware/ina219.cpp), [`src/hardware/ina219_regs.h`](../../src/hardware/ina219_regs.h), [`src/hardware/servo_wrapper.cpp`](../../src/hardware/servo_wrapper.cpp), [`src/display/oled.cpp`](../../src/display/oled.cpp), [docs/hardware/wiring.md](../hardware/wiring.md), [pinout.md](../hardware/pinout.md), [interfaces.md](../hardware/interfaces.md), [components.md](../hardware/components.md), [power.md](../hardware/power.md), [chip/PROTOCOL.md](../../chip/PROTOCOL.md) §1–2, [docs/api.md](../api.md) (mic, ranges, bump reflex).

**Could not load (blocked in this session), so not relied on:**

- Adafruit product 169 (micro servo current figures)
- the Waveshare 0.91" OLED wiki and product page
- Adafruit's SSD1306 datasheet copy
- NXP UM10204 (I2C-bus specification). The standard I2C figures in §1 are quoted without a fetched source.
- the raw GitHub copy of `ESP_I2S.cpp` (a local checkout was used instead)
