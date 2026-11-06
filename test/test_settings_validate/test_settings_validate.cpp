#include <cstring>
#include <unity.h>

#include "settings/settings.h"

void test_hostname_rejects_invalid() {
  TEST_ASSERT_FALSE(settingsValidateHostname(nullptr));
  TEST_ASSERT_FALSE(settingsValidateHostname(""));
  TEST_ASSERT_FALSE(settingsValidateHostname("-foo"));
  TEST_ASSERT_FALSE(settingsValidateHostname("foo-"));
  TEST_ASSERT_FALSE(settingsValidateHostname("foo_bar"));
  TEST_ASSERT_FALSE(
    settingsValidateHostname("abcdefghijklmnopqrstuvwxyz012345")
  );
}

void test_hostname_accepts_valid() {
  TEST_ASSERT_TRUE(settingsValidateHostname("tiny-engineer"));
  TEST_ASSERT_TRUE(
    settingsValidateHostname("abcdefghijklmnopqrstuvwxyz01234")
  );
  TEST_ASSERT_TRUE(settingsValidateHostname("Foo-1"));
}

void test_sleep_timeout() {
  TEST_ASSERT_FALSE(settingsValidateSleepTimeout(0));
  TEST_ASSERT_TRUE(settingsValidateSleepTimeout(1));
  TEST_ASSERT_TRUE(settingsValidateSleepTimeout(1440));
  TEST_ASSERT_FALSE(settingsValidateSleepTimeout(1441));
}

void test_continuous_timeout() {
  TEST_ASSERT_FALSE(settingsValidateContinuousTimeout(0));
  TEST_ASSERT_TRUE(settingsValidateContinuousTimeout(1));
  TEST_ASSERT_TRUE(settingsValidateContinuousTimeout(1440));
  TEST_ASSERT_FALSE(settingsValidateContinuousTimeout(1441));
}

void test_volume() {
  TEST_ASSERT_TRUE(settingsValidateVolume(0));
  TEST_ASSERT_TRUE(settingsValidateVolume(100));
  TEST_ASSERT_FALSE(settingsValidateVolume(101));
}

void test_loading() {
  TEST_ASSERT_TRUE(settingsValidateLoading("progress"));
  TEST_ASSERT_TRUE(settingsValidateLoading("sleep_inertia"));
  TEST_ASSERT_FALSE(settingsValidateLoading("other"));
  TEST_ASSERT_FALSE(settingsValidateLoading(nullptr));
}

void test_eyes_style() {
  TEST_ASSERT_TRUE(settingsValidateEyesStyle("classic"));
  TEST_ASSERT_TRUE(settingsValidateEyesStyle("kaomoji"));
  TEST_ASSERT_TRUE(settingsValidateEyesStyle("cover"));
  TEST_ASSERT_TRUE(settingsValidateEyesStyle("dots"));
  TEST_ASSERT_FALSE(settingsValidateEyesStyle("other"));
  TEST_ASSERT_FALSE(settingsValidateEyesStyle(nullptr));
}

void test_access_token() {
  char token64[65];
  char token65[66];

  memset(token64, 'a', 64);
  token64[64] = '\0';
  memset(token65, 'a', 65);
  token65[65] = '\0';

  TEST_ASSERT_TRUE(settingsValidateAccessToken(""));
  TEST_ASSERT_TRUE(settingsValidateAccessToken(token64));
  TEST_ASSERT_FALSE(settingsValidateAccessToken(token65));
  TEST_ASSERT_FALSE(settingsValidateAccessToken("foo\x01" "bar"));
  TEST_ASSERT_TRUE(settingsValidateAccessToken("two words"));
  TEST_ASSERT_FALSE(settingsValidateAccessToken(" leading"));
  TEST_ASSERT_FALSE(settingsValidateAccessToken("trailing "));
  TEST_ASSERT_FALSE(settingsValidateAccessToken(nullptr));
}

void test_wifi_ssid() {
  char ssid32[33];
  char ssid33[34];

  memset(ssid32, 's', 32);
  ssid32[32] = '\0';
  memset(ssid33, 's', 33);
  ssid33[33] = '\0';

  TEST_ASSERT_FALSE(settingsValidateWifiSsid(""));
  TEST_ASSERT_FALSE(settingsValidateWifiSsid(ssid33));
  TEST_ASSERT_FALSE(settingsValidateWifiSsid(nullptr));
  TEST_ASSERT_TRUE(settingsValidateWifiSsid("a"));
  TEST_ASSERT_TRUE(settingsValidateWifiSsid(ssid32));
}

void test_wifi_password() {
  char pass63[64];
  char pass64[65];

  memset(pass63, 'p', 63);
  pass63[63] = '\0';
  memset(pass64, 'p', 64);
  pass64[64] = '\0';

  TEST_ASSERT_TRUE(settingsValidateWifiPassword(""));
  TEST_ASSERT_TRUE(settingsValidateWifiPassword(pass63));
  TEST_ASSERT_FALSE(settingsValidateWifiPassword(pass64));
  TEST_ASSERT_FALSE(settingsValidateWifiPassword(nullptr));
}

void test_servo_ranges() {
  const ServoRanges valid = {{10, 20, 30, 40, 50}, {20, 30, 40, 50, 180}};
  const ServoRanges equal = {{10, 20, 30, 40, 50}, {20, 30, 40, 50, 50}};
  const ServoRanges over = {{10, 20, 30, 40, 50}, {20, 30, 40, 50, 181}};

  TEST_ASSERT_TRUE(valid.valid());
  TEST_ASSERT_FALSE(equal.valid());
  TEST_ASSERT_FALSE(over.valid());
}

void test_patch_names_the_first_invalid_field() {
  SettingsPatch patch;

  TEST_ASSERT_NULL(patch.invalidField());

  patch.volume = 50;
  patch.hostname = "tiny-engineer";
  TEST_ASSERT_NULL(patch.invalidField());

  patch.volume = 101;
  patch.rgbOrder = "XYZ";
  TEST_ASSERT_EQUAL_STRING("volume", patch.invalidField());

  patch.volume = 100;
  TEST_ASSERT_EQUAL_STRING("rgb_order", patch.invalidField());
}

void test_patch_names_its_setup_only_step() {
  SettingsPatch patch;

  patch.volume = 50;
  patch.hostname = "tiny-engineer";
  TEST_ASSERT_NULL(patch.setupOnlyStep());

  patch.oledRotate180 = false;
  TEST_ASSERT_EQUAL_STRING("oled", patch.setupOnlyStep());

  patch.servoRanges = ServoRanges{{10, 20, 30, 40, 50}, {20, 30, 40, 50, 60}};
  TEST_ASSERT_EQUAL_STRING("servo", patch.setupOnlyStep());
}

void test_rgb_order() {
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("RGB"));
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("RBG"));
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("GRB"));
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("GBR"));
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("BRG"));
  TEST_ASSERT_TRUE(settingsValidateRgbOrder("BGR"));
  TEST_ASSERT_FALSE(settingsValidateRgbOrder("RGBG"));
  TEST_ASSERT_FALSE(settingsValidateRgbOrder("grb"));
  TEST_ASSERT_FALSE(settingsValidateRgbOrder(nullptr));
}

void test_link_mode() {
  TEST_ASSERT_TRUE(settingsValidateLinkMode(""));
  TEST_ASSERT_TRUE(settingsValidateLinkMode("wifi"));
  TEST_ASSERT_TRUE(settingsValidateLinkMode("usb"));
  TEST_ASSERT_FALSE(settingsValidateLinkMode("ble"));
  TEST_ASSERT_FALSE(settingsValidateLinkMode(nullptr));
}

void test_link_mode_patch_rejects_empty() {
  SettingsPatch patch;
  patch.linkMode = "";
  TEST_ASSERT_EQUAL_STRING("link_mode", patch.invalidField());

  patch.linkMode = "wifi";
  TEST_ASSERT_NULL(patch.invalidField());
}

void test_link_mode_patch_denied_outside_setup() {
  TEST_ASSERT_NULL(settingsLinkModePatchDenied("usb", "", false, true));
  TEST_ASSERT_NULL(settingsLinkModePatchDenied("wifi", "wifi", true, false));
  TEST_ASSERT_EQUAL_STRING(
    "link_mode only in setup",
    settingsLinkModePatchDenied("usb", "wifi", true, false)
  );
  TEST_ASSERT_EQUAL_STRING(
    "link_mode only in setup",
    settingsLinkModePatchDenied("wifi", "usb", true, false)
  );
  TEST_ASSERT_EQUAL_STRING(
    "link_mode only in setup",
    settingsLinkModePatchDenied("wifi", "", false, false)
  );
}

void test_brain_host() {
  TEST_ASSERT_FALSE(settingsValidateBrainHost(nullptr));
  TEST_ASSERT_TRUE(settingsValidateBrainHost(""));
  TEST_ASSERT_TRUE(settingsValidateBrainHost("192.168.1.20"));
  TEST_ASSERT_TRUE(settingsValidateBrainHost("brain-pc.local"));
  TEST_ASSERT_TRUE(settingsValidateBrainHost("Brain1"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost("brain pc"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost("http://brain"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost("brain:47100"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost("-brain"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost(".brain"));
  TEST_ASSERT_FALSE(settingsValidateBrainHost("brain-"));

  char longest[SETTINGS_BRAIN_HOST_MAX_LEN + 2];
  memset(longest, 'a', sizeof(longest));
  longest[SETTINGS_BRAIN_HOST_MAX_LEN] = '\0';
  TEST_ASSERT_TRUE(settingsValidateBrainHost(longest));
  longest[SETTINGS_BRAIN_HOST_MAX_LEN] = 'a';
  longest[SETTINGS_BRAIN_HOST_MAX_LEN + 1] = '\0';
  TEST_ASSERT_FALSE(settingsValidateBrainHost(longest));
}

void test_brain_port() {
  TEST_ASSERT_FALSE(settingsValidateBrainPort(0));
  TEST_ASSERT_TRUE(settingsValidateBrainPort(1));
  TEST_ASSERT_TRUE(settingsValidateBrainPort(SETTINGS_DEFAULT_BRAIN_PORT));
  TEST_ASSERT_TRUE(settingsValidateBrainPort(65535));
  TEST_ASSERT_FALSE(settingsValidateBrainPort(65536));
  TEST_ASSERT_EQUAL_UINT16(47100, SETTINGS_DEFAULT_BRAIN_PORT);
}

void test_bump_threshold() {
  TEST_ASSERT_TRUE(settingsValidateBumpThreshold(0));
  TEST_ASSERT_TRUE(settingsValidateBumpThreshold(650));
  TEST_ASSERT_TRUE(settingsValidateBumpThreshold(3000));
  TEST_ASSERT_FALSE(settingsValidateBumpThreshold(3001));
  TEST_ASSERT_EQUAL_UINT16(300, SETTINGS_DEFAULT_BUMP_THRESHOLD_MA);
}

void test_mic_shift() {
  TEST_ASSERT_FALSE(settingsValidateMicShift(7));
  TEST_ASSERT_TRUE(settingsValidateMicShift(8));
  TEST_ASSERT_TRUE(settingsValidateMicShift(14));
  TEST_ASSERT_TRUE(settingsValidateMicShift(20));
  TEST_ASSERT_FALSE(settingsValidateMicShift(21));
  TEST_ASSERT_EQUAL_UINT8(14, SETTINGS_DEFAULT_MIC_SHIFT);
}

void test_patch_names_chip_fields() {
  SettingsPatch ok;
  ok.brainHost = "192.168.1.5";
  ok.brainPort = 47100;
  ok.bumpThresholdMa = 0;
  ok.micShift = 12;
  TEST_ASSERT_NULL(ok.invalidField());
  TEST_ASSERT_NULL(ok.setupOnlyStep());

  SettingsPatch host;
  host.brainHost = "bad host";
  TEST_ASSERT_EQUAL_STRING("brain_host", host.invalidField());

  SettingsPatch port;
  port.brainPort = 0;
  TEST_ASSERT_EQUAL_STRING("brain_port", port.invalidField());

  SettingsPatch bump;
  bump.bumpThresholdMa = 3001;
  TEST_ASSERT_EQUAL_STRING("bump_threshold_ma", bump.invalidField());

  SettingsPatch shift;
  shift.micShift = 21;
  TEST_ASSERT_EQUAL_STRING("mic_shift", shift.invalidField());
}

void test_mute_switch_field() {
  TEST_ASSERT_FALSE(SETTINGS_DEFAULT_MUTE_SWITCH);
  SettingsPatch on;
  on.muteSwitch = true;
  TEST_ASSERT_NULL(on.invalidField());
  // Not a setup-AP-only field: POST /settings applies it any time.
  TEST_ASSERT_NULL(on.setupOnlyStep());
  SettingsPatch off;
  off.muteSwitch = false;
  TEST_ASSERT_NULL(off.invalidField());
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_brain_host);
  RUN_TEST(test_brain_port);
  RUN_TEST(test_bump_threshold);
  RUN_TEST(test_mic_shift);
  RUN_TEST(test_patch_names_chip_fields);
  RUN_TEST(test_mute_switch_field);
  RUN_TEST(test_hostname_rejects_invalid);
  RUN_TEST(test_hostname_accepts_valid);
  RUN_TEST(test_sleep_timeout);
  RUN_TEST(test_continuous_timeout);
  RUN_TEST(test_volume);
  RUN_TEST(test_loading);
  RUN_TEST(test_eyes_style);
  RUN_TEST(test_access_token);
  RUN_TEST(test_wifi_ssid);
  RUN_TEST(test_wifi_password);
  RUN_TEST(test_servo_ranges);
  RUN_TEST(test_patch_names_the_first_invalid_field);
  RUN_TEST(test_patch_names_its_setup_only_step);
  RUN_TEST(test_rgb_order);
  RUN_TEST(test_link_mode);
  RUN_TEST(test_link_mode_patch_rejects_empty);
  RUN_TEST(test_link_mode_patch_denied_outside_setup);
  return UNITY_END();
}
