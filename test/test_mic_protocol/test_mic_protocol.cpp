#include <unity.h>

#include <cmath>
#include <cstring>
#include <vector>

#include "mic/mic_protocol.h"
#include "pins.h"

namespace {

uint32_t le32(const uint8_t* p) {
  return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
         (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

uint16_t le16(const uint8_t* p) {
  return static_cast<uint16_t>(p[0] | (p[1] << 8));
}

// INMP441 slot: 24-bit sample, MSB aligned in 32 bits, low byte zero.
int32_t slotFrom24(int32_t sample24) {
  return static_cast<int32_t>(static_cast<uint32_t>(sample24) << 8);
}

}  // namespace

void test_packet_constants_match_protocol() {
  TEST_ASSERT_EQUAL(16, kMicHeaderLen);
  TEST_ASSERT_EQUAL(256, kMicSamplesPerPacket);
  TEST_ASSERT_EQUAL(16 + 512, kMicPacketMaxLen);
  TEST_ASSERT_EQUAL(22050, SAMPLE_RATE);
}

void test_header_layout() {
  uint8_t buf[16];
  memset(buf, 0xEE, sizeof(buf));

  const size_t len = micWriteHeader(buf, 0x01020304u, 0xA0B0C0D0u, 0x000Fu, 256);

  TEST_ASSERT_EQUAL(16, len);
  TEST_ASSERT_EQUAL_MEMORY("CHP1", buf, 4);
  TEST_ASSERT_EQUAL_HEX32(0x01020304u, le32(buf + 4));
  TEST_ASSERT_EQUAL_HEX8(0x04, buf[4]);  // little-endian on the wire
  TEST_ASSERT_EQUAL_HEX32(0xA0B0C0D0u, le32(buf + 8));
  TEST_ASSERT_EQUAL_HEX16(0x000F, le16(buf + 12));
  TEST_ASSERT_EQUAL_UINT16(256, le16(buf + 14));
}

void test_heartbeat_header_has_zero_samples() {
  uint8_t buf[16];
  micWriteHeader(buf, 7, 1234, kMicFlagMuted, 0);
  TEST_ASSERT_EQUAL_UINT16(0, le16(buf + 14));
  TEST_ASSERT_EQUAL_HEX16(kMicFlagMuted, le16(buf + 12));
}

void test_seq_wraps() {
  uint8_t buf[16];
  micWriteHeader(buf, 0xFFFFFFFFu, 0, 0, 0);
  TEST_ASSERT_EQUAL_HEX32(0xFFFFFFFFu, le32(buf + 4));
  micWriteHeader(buf, 0xFFFFFFFFu + 1u, 0, 0, 0);
  TEST_ASSERT_EQUAL_HEX32(0u, le32(buf + 4));
}

void test_flag_bits() {
  TEST_ASSERT_EQUAL_HEX16(0, micFlagBits(false, false, false, false));
  TEST_ASSERT_EQUAL_HEX16(0x1, micFlagBits(true, false, false, false));
  TEST_ASSERT_EQUAL_HEX16(0x2, micFlagBits(false, true, false, false));
  TEST_ASSERT_EQUAL_HEX16(0x4, micFlagBits(false, false, true, false));
  TEST_ASSERT_EQUAL_HEX16(0x8, micFlagBits(false, false, false, true));
  TEST_ASSERT_EQUAL_HEX16(0xF, micFlagBits(true, true, true, true));
}

void test_sample_conversion_shift_and_saturation() {
  // slot >> 14 with default shift.
  TEST_ASSERT_EQUAL_INT16(1, micSampleFromSlot(1 << 14, 14));
  TEST_ASSERT_EQUAL_INT16(-1, micSampleFromSlot(-(1 << 14), 14));
  TEST_ASSERT_EQUAL_INT16(1000, micSampleFromSlot(1000 << 14, 14));
  TEST_ASSERT_EQUAL_INT16(-1000, micSampleFromSlot(-1000 * (1 << 14), 14));
  // Arithmetic shift rounds toward -inf.
  TEST_ASSERT_EQUAL_INT16(-1, micSampleFromSlot(-1, 14));
  TEST_ASSERT_EQUAL_INT16(0, micSampleFromSlot((1 << 14) - 1, 14));
  // Saturation.
  TEST_ASSERT_EQUAL_INT16(32767, micSampleFromSlot(INT32_MAX, 14));
  TEST_ASSERT_EQUAL_INT16(-32768, micSampleFromSlot(INT32_MIN, 14));
  TEST_ASSERT_EQUAL_INT16(32767, micSampleFromSlot(INT32_MAX, 8));
  // Shift 16 maps the slot's top half exactly (no saturation possible).
  TEST_ASSERT_EQUAL_INT16(32767, micSampleFromSlot(INT32_MAX, 16));
  TEST_ASSERT_EQUAL_INT16(-32768, micSampleFromSlot(INT32_MIN, 16));
  // Each step lower is 6 dB louder.
  const int32_t slot = slotFrom24(1024);
  TEST_ASSERT_EQUAL_INT16(2 * micSampleFromSlot(slot, 15), micSampleFromSlot(slot, 14));
}

void test_convert_takes_left_slot_only() {
  const int32_t stereo[6] = {
    slotFrom24(100) , 0x7FFFFFFF,
    slotFrom24(-200), 0x7FFFFFFF,
    slotFrom24(300) , INT32_MIN,
  };
  int16_t out[3] = {};

  micConvertLeftSlots(stereo, 3, 8, out);

  TEST_ASSERT_EQUAL_INT16(100, out[0]);
  TEST_ASSERT_EQUAL_INT16(-200, out[1]);
  TEST_ASSERT_EQUAL_INT16(300, out[2]);
}

void test_samples_little_endian() {
  const int16_t samples[3] = {0x1234, -2, -32768};
  uint8_t out[6] = {};
  micWriteSamplesLe(out, samples, 3);
  TEST_ASSERT_EQUAL_HEX8(0x34, out[0]);
  TEST_ASSERT_EQUAL_HEX8(0x12, out[1]);
  TEST_ASSERT_EQUAL_HEX8(0xFE, out[2]);
  TEST_ASSERT_EQUAL_HEX8(0xFF, out[3]);
  TEST_ASSERT_EQUAL_HEX8(0x00, out[4]);
  TEST_ASSERT_EQUAL_HEX8(0x80, out[5]);
}

void test_full_packet_roundtrip() {
  int32_t stereo[kMicSamplesPerPacket * 2];
  for (size_t i = 0; i < kMicSamplesPerPacket; i++) {
    stereo[i * 2] = slotFrom24(static_cast<int32_t>(i) * 64 - 8000);
    stereo[i * 2 + 1] = 0;
  }

  int16_t samples[kMicSamplesPerPacket];
  micConvertLeftSlots(stereo, kMicSamplesPerPacket, 14, samples);

  uint8_t packet[kMicPacketMaxLen];
  micWriteHeader(packet, 42, 1000, kMicFlagMoving, kMicSamplesPerPacket);
  micWriteSamplesLe(packet + kMicHeaderLen, samples, kMicSamplesPerPacket);

  TEST_ASSERT_EQUAL_UINT16(256, le16(packet + 14));
  for (size_t i = 0; i < kMicSamplesPerPacket; i++) {
    const int16_t decoded = static_cast<int16_t>(le16(packet + kMicHeaderLen + i * 2));
    TEST_ASSERT_EQUAL_INT16(static_cast<int16_t>(stereo[i * 2] >> 14), decoded);
  }
}

void test_level_silence_dc_and_tone() {
  std::vector<int16_t> block(256, 0);
  TEST_ASSERT_EQUAL_UINT16(0, micBlockLevelPermille(block.data(), block.size()));

  // Pure DC (offset mic) is not level.
  std::fill(block.begin(), block.end(), static_cast<int16_t>(5000));
  TEST_ASSERT_EQUAL_UINT16(0, micBlockLevelPermille(block.data(), block.size()));

  // Square wave +/-16384 around 0: RMS = 0.5 full scale.
  for (size_t i = 0; i < block.size(); i++) {
    block[i] = (i & 1) ? -16384 : 16384;
  }
  TEST_ASSERT_UINT16_WITHIN(1, 500, micBlockLevelPermille(block.data(), block.size()));

  // Full-scale square caps at 1000.
  for (size_t i = 0; i < block.size(); i++) {
    block[i] = (i & 1) ? -32768 : 32767;
  }
  TEST_ASSERT_UINT16_WITHIN(1, 1000, micBlockLevelPermille(block.data(), block.size()));
  TEST_ASSERT_EQUAL_UINT16(0, micBlockLevelPermille(block.data(), 0));
}

void test_level_smoothing_converges() {
  MicLevel level;
  TEST_ASSERT_EQUAL_UINT16(0, level.permille());
  level.update(1000);
  TEST_ASSERT_EQUAL_UINT16(300, level.permille());
  for (int i = 0; i < 40; i++) {
    level.update(1000);
  }
  TEST_ASSERT_UINT16_WITHIN(2, 1000, level.permille());
  for (int i = 0; i < 40; i++) {
    level.update(0);
  }
  TEST_ASSERT_UINT16_WITHIN(2, 0, level.permille());
}

void test_presence_needs_varying_left_slot() {
  MicPresence presence;
  int32_t stereo[8 * 2] = {};

  // Missing mic, pulled down: constant zeros never count.
  for (int i = 0; i < 10; i++) {
    presence.feed(stereo, 8);
  }
  TEST_ASSERT_FALSE(presence.present());

  // Right slot noise alone does not count.
  for (int i = 0; i < 8; i++) {
    stereo[i * 2 + 1] = i * 1000;
  }
  for (int i = 0; i < 10; i++) {
    presence.feed(stereo, 8);
  }
  TEST_ASSERT_FALSE(presence.present());

  // Left slot data: present after kMicPresentBlocks blocks.
  stereo[6] = slotFrom24(3);
  for (uint8_t i = 1; i < kMicPresentBlocks; i++) {
    presence.feed(stereo, 8);
    TEST_ASSERT_FALSE(presence.present());
  }
  presence.feed(stereo, 8);
  TEST_ASSERT_TRUE(presence.present());

  // Sticky.
  int32_t zeros[16] = {};
  presence.feed(zeros, 8);
  TEST_ASSERT_TRUE(presence.present());
}

void test_block_start_time() {
  // 256 frames at 22050 Hz = 11.6 ms -> 11 ms (floor).
  TEST_ASSERT_EQUAL_UINT32(989, micBlockStartMs(1000, 256, 22050));
  // Wraps with millis().
  TEST_ASSERT_EQUAL_UINT32(0xFFFFFFFFu - 10u + 5u, micBlockStartMs(5, 256, 22050));
  TEST_ASSERT_EQUAL_UINT32(1000, micBlockStartMs(1000, 256, 0));
}

void test_speaker_tail() {
  TEST_ASSERT_FALSE(micSpeakerRecent(false, 0, 100, kMicSpeakerTailMs));
  TEST_ASSERT_TRUE(micSpeakerRecent(true, 1000, 1000, kMicSpeakerTailMs));
  TEST_ASSERT_TRUE(micSpeakerRecent(true, 1000, 1299, kMicSpeakerTailMs));
  TEST_ASSERT_FALSE(micSpeakerRecent(true, 1000, 1300, kMicSpeakerTailMs));
  // millis() wrap.
  TEST_ASSERT_TRUE(micSpeakerRecent(true, 0xFFFFFF00u, 0x10u, kMicSpeakerTailMs));
}

void test_button_debounce_toggles_once_per_press() {
  MicButton button;
  uint32_t t = 0;

  // Idle high (not pressed).
  for (; t < 100; t += 10) {
    TEST_ASSERT_FALSE(button.update(false, t));
  }

  // Bounce: shorter than the debounce never fires.
  TEST_ASSERT_FALSE(button.update(true, 100));
  TEST_ASSERT_FALSE(button.update(false, 105));
  TEST_ASSERT_FALSE(button.update(true, 110));
  TEST_ASSERT_FALSE(button.update(true, 120));
  TEST_ASSERT_FALSE(button.update(false, 125));

  // Held: fires once after 30 ms stable.
  int presses = 0;
  for (t = 200; t < 400; t += 5) {
    presses += button.update(true, t) ? 1 : 0;
  }
  TEST_ASSERT_EQUAL(1, presses);
  TEST_ASSERT_TRUE(button.pressed());

  // Release does not fire.
  for (t = 400; t < 500; t += 5) {
    TEST_ASSERT_FALSE(button.update(false, t));
  }
  TEST_ASSERT_FALSE(button.pressed());

  // Second press fires again.
  presses = 0;
  for (t = 500; t < 600; t += 5) {
    presses += button.update(true, t) ? 1 : 0;
  }
  TEST_ASSERT_EQUAL(1, presses);
}

void test_button_never_fires_when_floating_high() {
  MicButton button;
  for (uint32_t t = 0; t < 100000; t += 11) {
    TEST_ASSERT_FALSE(button.update(false, t));
  }
}

void test_mute_state_pending_presses_toggle() {
  uint32_t presses = 0;
  uint32_t state = micPackMuteState(presses, false);
  TEST_ASSERT_FALSE(micEffectiveMuted(state, presses));

  // A press counted by the button task mutes at once, before the loop applies it.
  presses++;
  TEST_ASSERT_TRUE(micEffectiveMuted(state, presses));
  presses++;
  TEST_ASSERT_FALSE(micEffectiveMuted(state, presses));
  presses++;
  TEST_ASSERT_TRUE(micEffectiveMuted(state, presses));

  // Loop applies: same effective state, nothing pending.
  state = micPackMuteState(presses, micEffectiveMuted(state, presses));
  TEST_ASSERT_TRUE(micStateMuted(state));
  TEST_ASSERT_EQUAL_UINT32(presses, micStateApplied(state));
  TEST_ASSERT_TRUE(micEffectiveMuted(state, presses));
}

void test_mute_state_explicit_set_settles_presses() {
  uint32_t presses = 5;
  uint32_t state = micPackMuteState(0, false);
  TEST_ASSERT_TRUE(micEffectiveMuted(state, presses));  // 5 pending: odd
  // POST /mic {"muted":false} wins over presses counted before it.
  state = micPackMuteState(presses, false);
  TEST_ASSERT_FALSE(micEffectiveMuted(state, presses));
  // A press after it still toggles.
  TEST_ASSERT_TRUE(micEffectiveMuted(state, presses + 1));
}

void test_mute_state_counter_wrap() {
  const uint32_t applied = 0xFFFFFFFFu;
  const uint32_t state = micPackMuteState(applied, true);
  TEST_ASSERT_TRUE(micEffectiveMuted(state, applied));
  TEST_ASSERT_FALSE(micEffectiveMuted(state, applied + 1u));  // wraps to 0
  TEST_ASSERT_TRUE(micEffectiveMuted(state, applied + 2u));
}

// Switch mode (docs/adr/0004): GP7 level is the mute state.
void test_switch_closed_is_listening_open_is_muted() {
  const uint32_t state = micPackMuteState(0, true);  // button-mode state is ignored
  TEST_ASSERT_FALSE(micEffectiveMutedMode(true, true, state, 0));
  TEST_ASSERT_TRUE(micEffectiveMutedMode(true, false, state, 0));
  // Pending presses (switch closures) do not toggle anything in switch mode.
  TEST_ASSERT_FALSE(micEffectiveMutedMode(true, true, state, 7));
  TEST_ASSERT_TRUE(micEffectiveMutedMode(true, false, micPackMuteState(0, false), 3));
}

void test_button_mode_unchanged_by_switch_level() {
  uint32_t presses = 0;
  const uint32_t state = micPackMuteState(presses, false);
  TEST_ASSERT_FALSE(micEffectiveMutedMode(false, false, state, presses));
  TEST_ASSERT_FALSE(micEffectiveMutedMode(false, true, state, presses));
  TEST_ASSERT_TRUE(micEffectiveMutedMode(false, true, state, presses + 1));
  TEST_ASSERT_EQUAL(micEffectiveMuted(state, presses + 1),
                    micEffectiveMutedMode(false, false, state, presses + 1));
}

void test_switch_level_debounce() {
  // The debounced level (MicButton::pressed) is the switch: closed = pressed.
  MicButton sw;
  uint32_t t = 0;
  // Boot: open (pulled up). Starts "not closed" = muted, fail-safe.
  for (; t < 100; t += 5) {
    sw.update(false, t);
  }
  TEST_ASSERT_FALSE(sw.pressed());

  // Flip to I with contact bounce shorter than the debounce: ignored until stable.
  sw.update(true, 100);
  sw.update(false, 110);
  sw.update(true, 115);
  TEST_ASSERT_FALSE(sw.pressed());
  for (t = 120; t < 200; t += 5) {
    sw.update(true, t);
  }
  TEST_ASSERT_TRUE(sw.pressed());

  // A 20 ms glitch open is ignored: still listening.
  for (t = 200; t < 220; t += 5) {
    sw.update(false, t);
  }
  sw.update(true, 220);
  for (t = 225; t < 300; t += 5) {
    sw.update(true, t);
  }
  TEST_ASSERT_TRUE(sw.pressed());

  // Flip to O: muted once stable for the debounce time.
  for (t = 300; t < 300 + kMicButtonDebounceMs - 5; t += 5) {
    sw.update(false, t);
  }
  TEST_ASSERT_TRUE(sw.pressed());
  for (; t < 400; t += 5) {
    sw.update(false, t);
  }
  TEST_ASSERT_FALSE(sw.pressed());
}

void test_switching_modes_at_runtime() {
  // Button mode, unmuted, two presses counted and applied.
  uint32_t presses = 2;
  uint32_t state = micPackMuteState(presses, false);
  TEST_ASSERT_FALSE(micEffectiveMutedMode(false, true, state, presses));

  // Switch mode on: the level rules at once.
  TEST_ASSERT_TRUE(micEffectiveMutedMode(true, false, state, presses));
  // Flips while in switch mode count as presses; they must not leak later.
  presses += 5;
  TEST_ASSERT_TRUE(micEffectiveMutedMode(true, false, state, presses));

  // Switch mode off with the switch at O: button mode carries on muted, with
  // every press so far settled.
  state = micStateLeavingSwitch(false, presses);
  TEST_ASSERT_TRUE(micEffectiveMutedMode(false, false, state, presses));
  TEST_ASSERT_EQUAL_UINT32(presses & 0x7FFFFFFFu, micStateApplied(state));
  // The next press toggles as usual.
  TEST_ASSERT_FALSE(micEffectiveMutedMode(false, true, state, presses + 1));

  // Leaving switch mode at I carries on listening.
  state = micStateLeavingSwitch(true, presses);
  TEST_ASSERT_FALSE(micEffectiveMutedMode(false, true, state, presses));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_packet_constants_match_protocol);
  RUN_TEST(test_header_layout);
  RUN_TEST(test_heartbeat_header_has_zero_samples);
  RUN_TEST(test_seq_wraps);
  RUN_TEST(test_flag_bits);
  RUN_TEST(test_sample_conversion_shift_and_saturation);
  RUN_TEST(test_convert_takes_left_slot_only);
  RUN_TEST(test_samples_little_endian);
  RUN_TEST(test_full_packet_roundtrip);
  RUN_TEST(test_level_silence_dc_and_tone);
  RUN_TEST(test_level_smoothing_converges);
  RUN_TEST(test_presence_needs_varying_left_slot);
  RUN_TEST(test_block_start_time);
  RUN_TEST(test_speaker_tail);
  RUN_TEST(test_button_debounce_toggles_once_per_press);
  RUN_TEST(test_button_never_fires_when_floating_high);
  RUN_TEST(test_mute_state_pending_presses_toggle);
  RUN_TEST(test_mute_state_explicit_set_settles_presses);
  RUN_TEST(test_mute_state_counter_wrap);
  RUN_TEST(test_switch_closed_is_listening_open_is_muted);
  RUN_TEST(test_button_mode_unchanged_by_switch_level);
  RUN_TEST(test_switch_level_debounce);
  RUN_TEST(test_switching_modes_at_runtime);
  return UNITY_END();
}
