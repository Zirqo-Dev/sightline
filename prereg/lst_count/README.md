# LST dip count

Count only. The locked spec is `lst_count_spec_v1.md` (sha256 `d6dc5538b6e6ec0ec71ae98b522c658664775381a97fbd366a2ba94e4c3df052`). That file is not edited. Addenda go in `lst_count_deviations.md`, which was written before any capture.

T0 is the freeze time floored to the UTC hour. Each slot is a whole UTC hour since T0. The window ends at T0+336h.

The hourly workflow keeps running through the window. A run after the verdict does not write another capture. Once the verdict is in, a follow-up PR sets the workflow job to `if: false`.
