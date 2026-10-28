# Milestone 1 report

- Name: Shreya Amalapurapu
- UNI: sa4342

All results are from my own checks.

Spec versions used below: v1 is the first version (63 roles rules, 51 channels rules). v2.0 has 70 roles and 66 channels rules. v2.1 is v2.0 plus time limits in the checks (so they always print a RESULT line within 30 seconds) and a "protect your code" section in the README. The exact spec files for each run are printed in that run's transcript, since the agent reads /spec at the start.

## Approach 1 (single-file, A and B together)

### Run 1

- Component: both, starting from an empty /app
- Spec version: v2.0 single-file
- Job folder: jobs/2026-10-27__18-18-44/
- Check results: check_channels.sh 66 passed, 0 failed. check_roles.sh 70 passed, 0 failed, but that run was before the agent's last code changes. Roles reached 70/70 around step 20. Channels went 56/10, 59/7, 62/4, 63/3, 65/1, then 66/0.
- Run: 12m 43s, 61 steps, $0.76, no exceptions.
- Problems:
  - At step 33 the agent ran a patch script with a bug that deleted most of app.py. It kept going by running the compiled .pyc and wrote a wrapper.py that loads and patches it. This cost about 20 steps.
  - Because of that, the collected app has no .pyc, so it can't be started on its own and the checks couldn't be re-run on it. The final roles result is unverified.
  - Four commands hit the 30 second limit. One was the agent's own long command. check_roles.sh timed out once because it had no time limit yet. check_channels.sh timed out twice because its limit only covered HTTP requests, not WebSocket waits, so it was cut off with no RESULT line. Fixed in v2.1.

### Run 2 (rerun on the final spec)

- Component: both, starting from an empty /app
- Spec version: v2.1 single-file. After this run only the time limit in check_roles.sh changed (it now stops at the first rule after the deadline). That was tested against a slow app, a hung server and the real app (70/70 in 14 s), but not in another agent run.
- Job folder: jobs/2026-10-27__20-35-42/
- Check results: check_roles.sh 70 passed, 0 failed (step 49). check_channels.sh 66 passed, 0 failed (step 48). Roles went 14/56, 55/15, 65/5, 69/1, 70/0. Channels went 15/51, 30/36, 39/27, 58/8, 66/0. Work on B then broke 3 roles rules (63/3) and the agent fixed them, so its last roles run came after its last code change.
- Run: 13m 21s, 50 steps, $0.66, no exceptions. The collected app is plain source.
- Problems: one check_roles.sh run went over 30 seconds at step 6, when the cluster had to boot from scratch. The time limit fired correctly three times in the container, but in that case the script kept running the remaining rules after the deadline. Fixed as described above.

### Final /app

Job folder: jobs/2026-10-27__20-35-42/ (Run 1's app can't be started on its own). Both checks were also re-run locally on the collected app: roles 70/70 in 13.7 s, channels 66/66 in 3.9 s.

| Check | Passed | Failed | Notes |
| --- | --- | --- | --- |
| check_roles.sh | 70 | 0 | agent's last run, also confirmed locally |
| check_channels.sh | 66 | 0 | agent's last run, also confirmed locally |

## Approach 2 (single-file, one component at a time)

### Run 1 (component A)

- Component: A, roles and invitations
- Spec version: v1, 63 rules
- Job folder: jobs/2026-10-27__16-38-13/
- Check results: check_roles.sh 63 passed, 0 failed. The agent ran it four times: 47/16, 51/12, 62/1 (rule 24), 63/0.
- Run: 11m 39s, 16 steps, $0.085, no exceptions. The agent read the README first, only submitted after 63/0, and confirmed the baseline (health on all three ports, bad WebSocket token rejected, other nodes up after one was stopped).
- Problems: the invitation and join-mode routes returned 500 at first, and rule 24 (an admin changing another admin) was the last one fixed. It passed everything in about 11 minutes, so the hidden tests probably cover more than these 63 rules.

### Run 2 (component B)

- Component: B, channels and message history, built on the finished A code from Run 1 (copied into environment/app before the run)
- Spec version: v1, 51 channels rules, with the roles check kept as a regression
- Job folder: jobs/2026-10-27__17-27-11/
- Check results: check_channels.sh 51 passed, 0 failed and check_roles.sh 63 passed, 0 failed on the last runs. Channels went 5/46, 13/38, 28/23, 36/15, 38/13, 49/2 (rules 39 and 48), 51/0.
- Run: 11m 42s, 63 steps, $0.69, no exceptions. It read the README, ran both checks, confirmed the baseline and submitted.
- Problems: check_channels.sh hit the 30 second limit four times. The agent's server hung, every stuck request waited its full 5 seconds, and the check was cut off with no RESULT line. Fixed in v2 with one shared deadline for all requests.

### Final /app

Job folder: jobs/2026-10-27__17-27-11/ (A code plus B).

| Check | Passed | Failed | Notes |
| --- | --- | --- | --- |
| check_roles.sh | 63 | 0 | regression run at the end of Run 2 |
| check_channels.sh | 51 | 0 | v1 of the check |

## Approach 3 (staged, A and B together)

### Run 1

- Component: both, starting from an empty /app, as 3 roles stages and 4 channels stages
- Spec version: v2.1 staged. After this run the time limit went from 28 to 22 seconds, and the stage checks were changed to skip the WebSocket rules and the 55-post rule when they aren't in that stage.
- Job folder: jobs/2026-10-27__18-48-09/
- Check results: check_roles_all.sh 70 passed, 0 failed and check_channels_all.sh 66 passed, 0 failed on the last runs. Roles stages passed at steps 14, 18 and 29 (21, 23 and 26 rules). Channels.1 12/3 at step 31 and then passing, Channels.2 17/7 at step 51, Channels.3 went 2/8, 5/5, 9/1 and finally 10/0 at step 131, Channels.4 17/0 at step 132, then 66/0.
- Run: 32m 14s, 135 steps, $2.39 (close to the $3 limit), no exceptions. It followed the stage order and only submitted after both all-checks passed.
- Problems:
  - Channels.3 (WebSocket events, dense event ids) took steps 52 to 131 and about $1.7 of the total.
  - Four commands hit the 30 second limit because the agent chained a restart, a sleep and a check in one command. Several "0 passed, 17 failed" results were the check's time limit firing, not real failures: each stage check ran the whole flow first for setup, so a slow server ate the time budget. Fixed by skipping heavy rules a stage doesn't need (stage 4 went from about 6 s to under 1 s) and by telling the agent to run each check on its own.

### Final /app

Job folder: jobs/2026-10-27__18-48-09/. Both all-checks were also re-run locally on the collected app: roles 70/70 in 13.5 s, channels 66/66 in 6.3 s.

| Check | Passed | Failed | Notes |
| --- | --- | --- | --- |
| check_roles_all.sh | 70 | 0 | the agent's last roles run was at step 89, so the final code was confirmed locally |
| check_channels_all.sh | 66 | 0 | agent's last run at step 133, also confirmed locally |

## Approach 4 (staged, one component at a time)

### Run 1 (component A)

- Component: A, as 3 stages, starting from an empty /app
- Spec version: v2.1 staged. The time limit in the bash check was rewritten after this run.
- Job folder: jobs/2026-10-27__19-55-04/
- Check results: check_roles_all.sh 70 passed, 0 failed (step 8). The first run was 52/18.
- Run: 4m 22s, 12 steps, $0.06, no exceptions. Confirmed locally: 70/70 in 13.8 s.
- Problems:
  - The agent didn't use the stages. It never ran check_roles_1/2/3.sh, built everything in one step and only ran check_roles_all.sh, so this behaved like a single-file run.
  - check_roles_all.sh hit the 30 second limit twice. The agent's app keeps everything in one JSON file and rewrites and fsyncs it on every request, which is slow in the container. The time limit I had was meant to catch this but its RESULT line got swallowed (the trap ran inside a command substitution). Rewritten so that after 22 seconds every request fails at once and the script ends with a normal RESULT line.

### Run 2 (component B)

- Component: B as 4 stages, built on the A code from Run 1 (copied into environment/app), with the roles stages kept as a regression
- Spec version: v2.1 staged
- Job folder: jobs/2026-10-27__20-08-32/
- Check results: check_channels_all.sh 66 passed, 0 failed (step 49). Stages passed at steps 13, 25, 33 and 37 (15, 24, 10 and 17 rules). The roles regression went 21/0, 23/0, then Roles.3 21/5 at step 45 (broken by its own channels changes) and 26/0 at step 48.
- Run: 9m 27s, 50 steps, $0.46, no exceptions. This time the agent followed the stage order.
- Problems: one command hit the 30 second limit, but it was the agent's own curl probe of the bad WebSocket token, not one of the checks. The Roles.3 failures show that work on B can break A, which is why the roles check is kept as a regression.

### Final /app

Job folder: jobs/2026-10-27__20-08-32/ (A code from jobs/2026-10-27__19-55-04/ plus B). Both all-checks were also re-run locally on the collected app: roles 70/70 in 13.6 s, channels 66/66 in 6.7 s.

| Check | Passed | Failed | Notes |
| --- | --- | --- | --- |
| check_roles_all.sh | 70 | 0 | stages 21, 23 and 26 on the agent's last runs, full check confirmed locally |
| check_channels_all.sh | 66 | 0 | agent's last run at step 49, also confirmed locally |

## Comparison

Every approach ended at 70/70 on the roles checks and 66/66 on the channels checks, so on my own checks they are equal on correctness. However, they differ in cost:

| Approach | Layout | Steps | Time | Cost |
| --- | --- | --- | --- | --- |
| 1 (last run) | single-file, A and B together | 50 | 13m 21s | $0.66 |
| 2 | single-file, A then B (v1 specs) | 16 + 63 | 11m 39s + 11m 42s | $0.09 + $0.69 |
| 3 | staged, A and B together | 135 | 32m 14s | $2.39 |
| 4 | staged, A then B | 12 + 50 | 4m 22s + 9m 27s | $0.06 + $0.46 |

Together, single-file was much cheaper than staged: Approach 1 got the same result as Approach 3 in 50 steps and $0.66 instead of 135 steps and $2.39. Most of Approach 3's cost, about $1.7, went on the WebSocket events stage. I didn't see a case where the staged order helped the agent on my own checks, and in Approach 4's first run the agent ignored the stages completely. In Approach 3 and Approach 4's second run it followed them.

Doing one component at a time was cheap in total (Approach 4 $0.52, Approach 2 $0.78), because B only had to be built on top of working A code. The roles regression check also caught a real break there (Roles.3 at 21/5).

What I don't know: each approach was run once or twice, so the cost differences could be noise. The runs used different spec versions (Approach 2 used v1, the others v2.0 or v2.1), and these are my checks, not the hidden tests, so I can't say which approach scores best there. I expect the single-file layout to do at least as well as the staged one, because it passed the same checks for about a quarter of the cost and a third of the steps.
