# ASUS contest closeout package

This Stage I prototype lets a commuter compare five historical first-gantry windows, inspect why the selected conditions are not met, reveal later cohort outcomes separately, and change the final-gantry deadline. It provides a reproducible comparison, with no qualified departure recommendation or validated individual benefit.

## Canonical historical case

| Input | Value |
|---|---|
| Historical date | 24 August 2026 |
| Nominal first-gantry window | 06:45–07:00 |
| Final-gantry deadline | 07:30 |
| Maximum advance / delay | 15 / 30 minutes |
| Minimum predicted road-time reduction | 5 minutes |
| Schedule-shift cost preference | 0.5 |
| Changed-deadline scenario | 07:31, with outcomes still disclosed |

All clocks use `Asia/Taipei` (UTC+08:00). The five shifts are −30, −15, 0, +15 and +30 minutes; all remain visible, including the −30-minute option outside the user's bounds. The corridor covers passenger cars passing all five southbound National Freeway 1 gantries from 75 to 98 km. The windows and deadline refer to gantry passage, not leaving home or reaching work.

No candidate meets the complete numeric conditions. Advancing 15 minutes reduces the predicted road-time median by approximately **3.1 minutes**, below the required 5 minutes. Its schedule-shift cost is **7.5**, leaving a net difference of approximately **−4.4 minutes** under that preference. These are comparison quantities, not measured personal savings. Formal recommendations remain **0**; the complete lateness-risk difference remains **unknown**.

## Reproduce the records

Run these commands from the repository root with Node:

```sh
node scripts/contest_replay.cjs --scenario undisclosed
node scripts/contest_replay.cjs --scenario disclosed
node scripts/contest_replay.cjs --scenario changed-deadline
```

Each command writes deterministic `contest-replay-v1` JSON to stdout. It uses the same bundled public adapter as the interface and records its actual SHA256, inputs, all five rows, independent numeric checks, policy qualification, disclosure state and limitations. It requires no traffic request, model execution or running service.

| Record | Disclosure boundary |
|---|---|
| [undisclosed.json](replay/undisclosed.json) | Later cohort outcomes are absent; each `observed` value is `null`. |
| [disclosed.json](replay/disclosed.json) | Includes only the bundled partial cohort measurements for the original 07:30 deadline. |
| [changed-deadline.json](replay/changed-deadline.json) | Uses 07:31; every observed `ontime_share` is `null`, while forecasts remain unchanged. |

The disclosed road-time median difference between the nominal and 15-minute-earlier cohorts is approximately 3.85 minutes. The cohorts contain different vehicles; this is not the causal saving for one person changing departure time. Other missing quantiles and calibration support remain `null`, while a known observed zero remains zero. See [reproducibility details](../reproducibility.md#賽事比較紀錄contest-replay-v1) for independent arithmetic, byte comparisons and test evidence.

## Completed interface checks

Actual-browser QA has checked the canonical setup, five visible candidates, the explanation that no option meets the selected conditions, manual selection of the 15-minute-earlier option, and its displayed **3.1 / 7.5 / −4.4** comparison. It has also checked separate outcome disclosure and the changed 07:31 deadline showing **Unknown** for observed on-time proportions.

Manual selection is an exploratory user action and does not qualify a policy. These checks establish interface behavior; they do not establish commuter effects, hardware performance or human acceptance of the submission.

## Media handoff

The validated English media files comprise an **18-main-page PDF**, an editable presentation, and a **160-second silent video** edited from actual-interface historical replay evidence. The video demonstrates setup, comparison, refusal, disclosure and the changed deadline. It is an edited historical demonstration, with no recorded testimonial or validated benefit claim.

**Local media QA is complete.** All 18 PDF pages were rendered and inspected; the PDF is 610,980 bytes. The editable PPTX passed package/import/layout validation with seven native tables. The 160-second H.264 video has English captions, no audio stream, and passed full decoding plus representative-frame readability checks. Native Microsoft PowerPoint and Google Slides were not tested. Exact file sizes and SHA256 hashes are in the [media manifest](media-manifest.json). Media binaries are delivered separately; this repository does not publish the video or certify an official submission. The PDF main text must be no more than **20 pages** and the PDF no more than **15 MB**. The prepared video must remain within **180 seconds**. The official form treats the video as optional; the package nevertheless prepares one for the user to consider.

## Evidence and limits

- The public adapter contains previously exposed calibration aggregates, not an independent holdout or a prospective personal-benefit trial. The empirical display method does not make the departure policy qualified.
- q50 and q90 are quantile points, not confidence intervals, maximum travel times or personal on-time guarantees. Only partial D45 cohort measurements are bundled; another deadline cannot inherit the D45 on-time proportion.
- The [fixed research delivery snapshot](../research/morning-overview-2026-10-27/README.md) reports 36 sealed and 24 missing of 60 scheduled issues. Those are delivery counts, not evidence of savings or reduced lateness. The truth gate cannot meet its original all-five-day requirement because the first required truth day was incomplete; this does not mean all five days have ended. Truth, effects and confidence intervals remain unverified.
- No UGen hardware measurement or demonstrated hardware execution supports this package. Hardware capability, individual time savings, complete lateness-risk change and real-world effectiveness remain unverified.
- The package does not introduce new traffic collection, training, inference, research evaluation or schedule changes. Private operational receipts, raw/truth data, weights and credentials are excluded.

## Human submission steps

The official deadline is **28 October 2026 at 17:00, Asia/Taipei (UTC+08:00)**. Requirements and the entry point are provided by the [official ASUS contest page](https://contest.bhuntr.com/tw/39jg9vimiynrhlksze/home/).

Confirm the selected Stage II platform and the form's fixed M2 or USB hardware format. The selection is the user's decision. The user must complete and sign the required consent, review the final files and submit the official form. No signature, platform selection, formal submission or public video posting is implied by preparing this repository package. Use the [submission checklist](submission-checklist.md) to distinguish completed technical checks from the remaining delivery and human steps.
