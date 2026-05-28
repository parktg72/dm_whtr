#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic smoke pipeline runner tests."""

import unittest

import run_pipeline_smoke_test


class TestRunPipelineSmokeTest(unittest.TestCase):
    def test_build_command_plan_default_sequence_revalidates_after_tests_before_analysis(self):
        plan = run_pipeline_smoke_test.build_command_plan(python_executable="pythonX")
        commands = [step.command for step in plan]
        labels = [step.label for step in plan]

        self.assertEqual(labels, [
            "Generate synthetic SQLite database",
            "Build analytical cohort",
            "Run full unittest discovery",
            "Validate final cohort QA gate",
            "Run trajectory and Cox analysis",
        ])
        unittest_command = ["pythonX", "-m", "unittest", "discover", "-v"]
        validation_command = ["pythonX", "validate_cohort_output.py", "--cohort", "data/cohort_analytical.csv", "--lag1y", "data/cohort_analytical_lag1y.csv"]
        analysis_command = ["pythonX", "analyze_trajectories.py"]
        self.assertLess(commands.index(unittest_command), commands.index(validation_command))
        self.assertLess(commands.index(validation_command), commands.index(analysis_command))

    def test_build_command_plan_can_skip_analysis_for_fast_environment_checks(self):
        plan = run_pipeline_smoke_test.build_command_plan(python_executable="pythonX", skip_analysis=True)
        commands = [step.command for step in plan]
        labels = [step.label for step in plan]

        self.assertEqual(labels, [
            "Generate synthetic SQLite database",
            "Build analytical cohort",
            "Run full unittest discovery",
            "Validate final cohort QA gate",
        ])
        self.assertLess(
            commands.index(["pythonX", "-m", "unittest", "discover", "-v"]),
            commands.index(["pythonX", "validate_cohort_output.py", "--cohort", "data/cohort_analytical.csv", "--lag1y", "data/cohort_analytical_lag1y.csv"]),
        )
        self.assertNotIn(["pythonX", "analyze_trajectories.py"], commands)


if __name__ == "__main__":
    unittest.main()
