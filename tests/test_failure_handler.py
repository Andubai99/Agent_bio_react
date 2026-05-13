from __future__ import annotations

import unittest

from agent.failure_handler import FailureContext, FailureDecisionType, FailurePolicy, FailureStage


class FailurePolicyTests(unittest.TestCase):
    def test_verify_failed_retries_once_then_aborts(self) -> None:
        policy = FailurePolicy()
        context = FailureContext(
            stage=FailureStage.VERIFICATION,
            code="VERIFY_FAILED",
            message="Vision verification failed.",
            instruction_index=1,
            loop_step=1,
            page_changed=False,
        )

        first = policy.decide(context)
        second = policy.decide(context)

        self.assertEqual(first.decision, FailureDecisionType.RETRY_INSTRUCTION)
        self.assertEqual(first.attempt, 1)
        self.assertEqual(first.max_attempts, 1)
        self.assertEqual(second.decision, FailureDecisionType.ABORT)
        self.assertEqual(second.attempt, 2)

    def test_page_change_comparison_failed_falls_back_once(self) -> None:
        policy = FailurePolicy()
        context = FailureContext(
            stage=FailureStage.PAGE_CHANGE,
            code="PAGE_CHANGE_COMPARISON_FAILED",
            message="comparison failed",
            instruction_index=1,
            loop_step=1,
        )

        decision = policy.decide(context)

        self.assertEqual(decision.decision, FailureDecisionType.FALLBACK_SINGLE_SCREENSHOT_VERIFICATION)

    def test_non_recoverable_action_aborts(self) -> None:
        policy = FailurePolicy()
        context = FailureContext(
            stage=FailureStage.ACTION,
            code="UNKNOWN_ACTION",
            message="Unsupported action type.",
            instruction_index=1,
            loop_step=1,
        )

        decision = policy.decide(context)

        self.assertEqual(decision.decision, FailureDecisionType.ABORT)
        self.assertEqual(decision.max_attempts, 0)

    def test_startup_failure_aborts(self) -> None:
        policy = FailurePolicy()
        context = FailureContext(
            stage=FailureStage.STARTUP,
            code="OMNIPARSER_UNAVAILABLE",
            message="OmniParser service failed to start.",
        )

        decision = policy.decide(context)

        self.assertEqual(decision.decision, FailureDecisionType.ABORT)
        self.assertEqual(decision.max_attempts, 0)

    def test_reset_instruction_clears_retry_count(self) -> None:
        policy = FailurePolicy()
        context = FailureContext(
            stage=FailureStage.REASONER,
            code="REASONER_FAILED",
            message="temporary reasoner failure",
            instruction_index=2,
            loop_step=1,
        )

        self.assertEqual(policy.decide(context).decision, FailureDecisionType.RETRY_REASONER)
        self.assertEqual(policy.decide(context).decision, FailureDecisionType.ABORT)

        policy.reset_instruction(2)

        self.assertEqual(policy.decide(context).decision, FailureDecisionType.RETRY_REASONER)


if __name__ == "__main__":
    unittest.main()
