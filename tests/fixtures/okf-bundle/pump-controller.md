---
name: project_pump-controller
description: Pump controller firmware notes; the relay board needs a 200 ms settle before reading the flow meter.
metadata:
  type: project
  tags: [pump, firmware]
---
# Pump controller

The relay board needs a 200 ms settle before the flow meter reading is
stable. Reading earlier gives a zero and the controller assumes a dry run.
