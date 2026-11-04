# Robot layout and movement

The robot sits in a chair beside a desk. A miniature laptop and a service bell sit on the desk in front of it. Five micro servos drive the pose; limits and channel IDs are in [`include/servos.h`](../include/servos.h).

| Index | Servo | Motion | At `min` | At `max` | Safe range (°) |
|------:|-------|--------|----------|----------|----------------|
| 0 | **Head** | Face up / down (pitch) | down | up | 80 – 100 (stock) |
| 1 | **Neck** | Head left / right (yaw) | right | left | 80 – 100 (stock) |
| 2 | **Left hand** | Hand up / down | up | down | 80 – 100 (stock) |
| 3 | **Right hand** | Hand up / down (inverted scale) | down | up | 80 – 100 (stock) |
| 4 | **Body** | Whole torso left / right | right | left | 80 – 100 (stock) |

Stock defaults match `SERVO_SPECS` in [`include/servos.h`](../include/servos.h): **80°–100°** on every joint when NVS is empty (mid 90°). After setup AP guided calibration the wider per-build limits are stored in NVS. Factory reset keeps the saved ranges. Retune them from the setup wizard servos hub (**Calibrate servos**) when AP mode is open, or **Skip** to keep the current values.

Animations author poses in **−1..1** relative to each joint’s saved min/max (`−1` = min, `0` = mid, `+1` = max). Horn offset after assembly is absorbed by calibration; firmware maps those poses with `servoNormToDeg`. Right-hand rest is still electrical min (`−1`); left-hand rest is electrical max (`+1`).

**Head** tilts the face toward or away from the laptop. **Neck** pans the head side to side. **Hands** lift and lower over the keyboard; the forearms have no elbow servo — the arm linkage is fixed, so only the hand joint moves. **Left** and **right** hand servos use opposite scales: on the left, higher angle is down; on the right, higher angle is up (rest pose for typing is left at `max`, right at `min` — both over the keyboard). Setup AP guided calibration sets those keyboard bounds first, then the upper bounds. **Body** rotates the whole upper body in the chair while the base stays put.

## Saved, soft and hard limits

- **Saved** — the NVS calibration above; poses map onto it.
- **Hard** — compile-time caps `SERVO_HARD_MIN_DEG` / `SERVO_HARD_MAX_DEG` in [`include/servos.h`](../include/servos.h): **10°–170°** on every joint, inside the 0–180° electrical clamp. Runtime code never goes past them; a saved range wider than that (possible from the setup wizard) is clamped at runtime.
- **Soft** — RAM-only limits that start equal to saved (clamped into hard). With the optional INA219 current sensor, a bump narrows the soft limit on the side the joint was travelling. Animations, `POST /pose` and `POST /test/servo` clamp to the soft limits. `POST /bumps/clear`, `POST /ranges` and a reboot reset them.

`POST /ranges` saves a new calibration outside setup mode (inside the hard caps, span ≥ 6°) and `POST /ranges/explore` probes one joint toward its hard cap until it bumps — [api.md](api.md#get-ranges).

Command a single joint for bench checks:

```bash
curl -X POST "http://tiny-engineer.local/test/servo" -H "Content-Type: application/json" -d '{"index":0,"angle":90}'
```

Use angles inside the saved safe range on the assembled robot. During setup AP, `POST /setup/servo` can command the full 0–180° electrical range so you can find those limits.

Related: [servos.md](hardware/servos.md) (PWM and electrical limits), [api.md](api.md) (`/test/servo`, `/anim`).
