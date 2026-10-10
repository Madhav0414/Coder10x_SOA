# What went wrong, and how it was fixed

Each of these is covered by a test in `tests/`.

**Filling pressure is invisible to a BP cuff.** The first fit, from vitals alone,
called congested patients dry; it would have suggested fluids for a drowning
patient. Adding the signs clinicians use for the Stevenson profile (JVP, oedema,
weight, and later capillary refill for perfusion) fixed both axes.

**Without a congestion penalty the model advised against diuretics.** High
filling pressure has to cost contractility, as it does clinically.

**Trusting the latest reading.** The twin first took its state from the most
recent reading. Cardiac index comes largely from cuff pulse pressure, and that
single-reading error was about 0.55 L/min/m². Fitting the whole chart jointly
roughly halved it.

**An alert rule that suppressed escalation.** If the twin projected an imminent
crossing and no ward drug helped, it stayed silent. Imminent crossings now always
alert; the action is escalation.

**A cadence comparison that wasn't like for like.** Observation noise shared a
random stream with patient generation, so changing the charting interval changed
the patients. It now has its own stream.

**An alarm-reduction claim that didn't hold.** An earlier version reported "81%
fewer alarms than NEWS2". It compared NEWS2 per reading with Prahari per patient.
Scored fairly, round by round with thresholds swept, the advantage disappears, so
the claim was withdrawn and the system repositioned behind NEWS2.

**Forecasts that opened with a jump.** Baroreflex baselines taken from averages
made every forecast start with a spurious hour-long correction. They are now
backed out so the fitted present is an equilibrium of the model.

**The home twin's first physics.** No heart decompensated within seven litres,
resting heart rate fell as fluid built up, and activity rose. Too little retained
fluid reached the central circulation, the baroreflex alone ignores the
sympathetic drive of rising filling pressure, and extra cardiac output doesn't
make anyone walk further. All three were corrected before any home experiment
was run; the alarm threshold was fixed in advance and not tuned afterwards.

**Two simulations at once.** SciPy's LSODA integrator cannot run two problems
concurrently, and the API answers requests on a thread pool. A ward refit and a
what-if arriving together crashed one of them. Integrations now take turns.

**A demo server too slow to fit live.** On a free hosting plan one round of the
hourly feed took up to two minutes to fit, and a visitor who switched feeds and
then opened a patient waited behind it; two requests for the same round also
ran the fit twice. Deploys now fit every round of the opening ward during the
build and load them from disk, and concurrent requests for a round share one fit.
