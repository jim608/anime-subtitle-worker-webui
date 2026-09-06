# Owned reconciliation deployment

`safe-update-stack.sh` accepts an optional `RECONCILIATION_HOLD_ID` only when the
running Worker already reports the same durable reconciliation admission hold
and `paused=true`. Create that hold through the existing controlled Worker API
after draining existing work; this option does not create an authorization or
allow an operator to bypass any breaker, checkpoint, source hold or Gate.

The deployed scheduler can report this intentional hold as `deployment_hold`.
The final health probe accepts that state only after rechecking the owned hold;
the normal fresh heartbeat, running-container and no-problem checks still apply.
Without this option the existing deployment behavior is unchanged.

In this explicitly verified mode, post-retire deployment failures preserve the
current databases, images, checkpoints and evidence. They do not restore a
historical Production database. The formal pause entry is reasserted when the
container is reachable, the backup is marked failed, and the deployment owner
releases its own mutex on exit. Admission protection is not released. If the
container cannot be checked, keep maintenance protection and inspect the failure;
do not delete its hold/lock/latch or declare recovery.

On success, deployment completion does not itself resume reconciliation admission.
The existing controlled recovery must verify actual runtime/configuration, sealed
reconciliation evidence, source holds and fresh breaker tests, then initialize the
appropriate frozen Gate and resume claims. No old cohort results are copied.

Tests cover a real shell failure handler with isolated command stubs, ensuring
no rollback copy/move/container-retirement operations are called, plus acceptance
of a verified intentional hold and rejection of an unverified deployment hold.
