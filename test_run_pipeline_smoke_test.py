#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic smoke pipeline runner tests."""

import unittest

import run_pipeline_smoke_test


class TestRunPipelineSmokeTest(unittest.TestCase):
    def test_build_command_plan_default_sequence_includes_stop_the_line_qa_before_analysis(self):
        plan = run_pipeline_smoke_test.build_command_plan(python_executable="pythonX")
        commands = [step.command for step in plan]
        labels = [step.label for step in plan]

        self.assertEqual(labels, [
            "Generate synthetic SQLite database",
            "Build analytical cohort",
            "Validate final cohort QA gate",
            "Run full unittest discovery",
            "Run trajectory and Cox analysis",
        ])
        self.assertLess(
            commands.index(["pythonX", "validate_cohort_output.py", "--cohort", "data/cohort_analytical.csv", "--lag1y", "data/cohort_analytical_lag1y.csv"]),
            commands.index(["pythonX", "analyze_trajectories.py"]),
        )

    def test_build_command_plan_can_skip_analysis_for_fast_environment_checks(self):
        plan = run_pipeline_smoke_test.build_command_plan(python_executable="pythonX", skip_analysis=True)
        commands = [step.command for step in plan]

        self.assertIn(["pythonX", "-m", "unittest", "discover", "-v"], commands)
        self.assertNotIn(["pythonX", "analyze_trajectories.py"], commands)


if __name__ == "__main__":
    unittest.main()
