---
type: Reference
title: Sensor calibration
description: Soil moisture probe offsets after the 2.1 firmware change.
tags: [sensors, firmware]
modified: 2026-06-15
generated:
  by: process:orchard-distill/0.3
  at: 2026-06-14T18:00:00Z
verified:
  - by: process:calibrate/2.1
    at: 2026-06-15T07:00:00Z
---
# Sensor calibration

Every soil moisture probe reads four percent high after the 2.1 firmware.
Apply a minus four offset in the controller, not in the dashboard.
