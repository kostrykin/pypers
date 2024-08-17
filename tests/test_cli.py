import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch

import repype.cli
from . import testsuite
from . import test_status


class StatusReaderConsoleAdapter__progress(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.status = repype.status.Status(path = self.tempdir.name)
        self.status_reader = repype.cli.StatusReaderConsoleAdapter(self.status.filepath)
        self.status_reader.__enter__()

    def tearDown(self):
        self.status_reader.__exit__(None, None, None)
        self.tempdir.cleanup()

    @testsuite.with_temporary_paths(1)
    def test(self, path):
        with testsuite.CaptureStdout() as stdout:
            for item_idx, item in enumerate(repype.status.progress(self.status, range(3), info = 'progress')):

                test_status.wait_for_watchdog()
                if item_idx == 0:
                    self.assertEqual(
                        str(stdout),
                        "{'status': {'info': 'progress'}, 'progress': 0.0, 'step': 0, 'max_steps': 3}\r",
                    )

        # Verify that there have been three iterations, i.e. `item_idx = 0`, `item_idx = 1`, `item_idx = 2`
        self.assertEqual(item_idx, 2)

# ---


class DelayedTask(repype.task.Task):

    def store(self, *args, **kwargs):
        # Delay Task.store by 1 second, so that intermediates don't collapse too quickly
        time.sleep(1)
        return super().store(*args, **kwargs)
    

class DefectiveTask(repype.task.Task):

    def store(self, *args, **kwargs):
        raise testsuite.TestError()


class run_cli_ex(unittest.TestCase):

    stage1_cls = testsuite.create_stage_class(id = 'stage1', inputs = ['input'  ], outputs = ['output1'])
    stage2_cls = testsuite.create_stage_class(id = 'stage2', inputs = ['output1'], outputs = ['output2'])

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root_path = pathlib.Path(self.tempdir.name)
        testsuite.create_task_file(
            self.root_path,
            'runnable: true' '\n'
            'pipeline:' '\n'
            '- tests.test_task.Task__create_pipeline.stage1_cls' '\n'
            '- tests.test_task.Task__create_pipeline.stage2_cls' '\n'
        )
        testsuite.create_task_file(
            self.root_path / 'task-2',
            'config:' '\n'
            '  stage1:' '\n'
            '    key1: value1' '\n'
        )
        testsuite.create_task_file(
            self.root_path / 'task-3',
            'config:' '\n'
            '  stage2:' '\n'
            '    key2: value2' '\n'
        )

    def tearDown(self):
        self.tempdir.cleanup()

    @patch.object(repype.batch.Batch, 'run')
    def test(self, mock_batch_run):
        with testsuite.CaptureStdout() as stdout:
            ret = repype.cli.run_cli_ex(path = self.tempdir.name)
            self.assertTrue(ret)
            mock_batch_run.assert_not_called()
            self.assertEqual(
                str(stdout),
                '\n'
                '3 task(s) selected for running' '\n'
                'DRY RUN: use "--run" to run the tasks instead' '\n',
            )

    @patch.object(repype.batch.Batch, 'run')
    def test_run(self, mock_batch_run):
        with testsuite.CaptureStdout() as stdout:
            ret = repype.cli.run_cli_ex(path = self.tempdir.name, run = True)
            self.assertTrue(ret)
            mock_batch_run.assert_called_once()
            self.assertIn('status', mock_batch_run.call_args_list[0].kwargs)
            self.assertEqual(len(mock_batch_run.call_args_list[0].args), 1)
            self.assertEqual([type(rc) for rc in mock_batch_run.call_args_list[0].args[0]], [repype.batch.RunContext] * 3)
            self.assertEqual(
                str(stdout),
                '\n'
                '3 task(s) selected for running' '\n',
            )

    def test_run_integrated(self):
        with testsuite.CaptureStdout() as stdout:
            ret = repype.cli.run_cli_ex(path = self.tempdir.name, run = True, task_cls = DelayedTask)
            self.assertTrue(ret)
            self.assertEqual(
                str(stdout),
                f'\n'
                f'3 task(s) selected for running' '\n'
                f'  \n'
                f'  (1/3) Entering task: {self.root_path.resolve()}' '\n'
                f'  Starting from scratch' '\n'
                f'  Storing results...' '\r'
                f'  Results have been stored ✅' '\n'
                f'  \n'
                f'  (2/3) Entering task: {self.root_path.resolve()}/task-2' '\n'
                f'  Starting from scratch' '\n'
                f'  Storing results...' '\r'
                f'  Results have been stored ✅' '\n'
                f'  \n'
                f'  (3/3) Entering task: {self.root_path.resolve()}/task-3' '\n'
                f'  Picking up from: {self.root_path.resolve()} (stage2)' '\n'
                f'  Storing results...' '\r'
                f'  Results have been stored ✅' '\n'
            )

    @patch.object(repype.status.Status, 'intermediate')  # Suppress the `Storing results...` intermediate, sometimes not captured quickly enough
    def test_internal_error(self, mock_status_intermediate):
        with testsuite.CaptureStdout() as stdout:
            ret = repype.cli.run_cli_ex(path = self.tempdir.name, run = True, task_cls = DefectiveTask)
            self.assertFalse(ret)
            self.assertIn(
                f'\n'
                f'3 task(s) selected for running' '\n'
                f'  \n'
                f'  (1/3) Entering task: {self.root_path.resolve()}' '\n'
                f'  Starting from scratch' '\n'
                f'  ' '\n'
                f'  🔴 An error occurred while processing the task {self.root_path.resolve()}:' '\n'
                f'  --------------------------------------------------------------------------------' '\n'
                f'  Traceback (most recent call last):',
                str(stdout),
            )
            self.assertIn(
                f'  tests.testsuite.TestError' '\n'
                f'  --------------------------------------------------------------------------------' '\n'
                f'\n'
                f'🔴 Batch run interrupted' '\n',
                str(stdout),
            )