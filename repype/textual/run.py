import traceback

import bidict
import repype.status
from textual import (
    log,
    work,
)
from textual.binding import (
    Binding,
)
from textual.containers import (
    Vertical,
)
from textual.screen import (
    ModalScreen,
)
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Label,
    ProgressBar,
)
from .confirm import ConfirmScreen


class StatusReaderAdapter(repype.status.StatusReader):

    def __init__(self, filepath, run_screen):
        self.screen = run_screen
        super().__init__(filepath)

    def handle_new_status(self, *args, **kwargs):
        self.screen.handle_new_status(*args, **kwargs)


class RunScreen(ModalScreen[bool]):

    BINDINGS = [
        Binding('ctrl+c', 'cancel', 'Cancel', priority = True),
        Binding('escape', 'close', 'Close'),
    ]

    def __init__(self, contexts):
        super().__init__()
        self.sub_title = 'Run tasks'
        self.contexts = contexts
        self.task_ids = bidict.bidict()  # path -> task_id
        self.current_task_path = None
        self.intermediate = None
        self.intermediate_extra = ProgressBar()
        self.success = False
        self.finished_tasks = set()  # paths

    def compose(self):
        yield Header()

        for task_id, rc in enumerate(self.contexts, start = 1):
            self.task_ids[str(rc.task.path.resolve())] = task_id
            with Collapsible(collapsed = True, id = f'run-task-{task_id}'):
                vertical = Vertical(id = f'run-task-{task_id}-container')
                vertical.styles.height = 'auto'
                yield vertical

        yield Footer()

    def update_task_state(self, task_path = None, task_id = None):
        assert task_path or task_id
        if task_path:
            task_id = self.task_ids[str(task_path)]
        if task_id:
            task_path = self.task_ids.inv[task_id]
        collapsible = self.query_one(f'#run-task-{task_id}')
        collapsible.title = str(task_path)
        if str(task_path) in self.finished_tasks:
            collapsible.title += ' (done)'

    def on_mount(self):
        for rc in self.contexts:
            self.update_task_state(task_path = rc.task.path.resolve())
        self.run_batch()

    def action_cancel(self):
        if self.app.batch.task_process:
            screen = ConfirmScreen('Cancel the unfinished tasks?', default = 'no')
            async def confirm(yes):
                if yes:
                    await self.app.batch.cancel()
            self.app.push_screen(screen, confirm)

    def action_close(self):
        if self.app.batch.task_process is None:
            self.dismiss(self.success)
        else:
            self.app.notify('Cancel before closing, or wait to finish', severity='error', timeout=3)

    @work(exclusive = True)
    async def run_batch(self):
        with repype.status.create() as status:
            async with StatusReaderAdapter(status.filepath, self):
                success = await self.app.batch.run(self.contexts, status = status)

                # Report the success of the batch run
                log('StatusReader.run_batch', success = success)
                self.success = success

    def handle_new_status(self, parents, positions, status, intermediate):
        log('StatusReader.handle_new_status', status = status, intermediate = intermediate)
        if isinstance(status, dict) and (task_path := status.get('task')):
            self.current_task_path = task_path
        else:
            task_path = self.current_task_path

        task_id = self.task_ids[task_path] if task_path else None
        task_container = self.screen.query_one(f'#run-task-{task_id}-container') if task_id else None
        try:
            if task_id:
                task_collapsible = self.screen.query_one(f'#run-task-{task_id}')
                task_collapsible.collapsed = False
                self.ends_with_rule = False

            # If the new status is intermediate...
            if intermediate:

                # ...and empty, then clear the previous intermediate status
                if status is None and self.intermediate:
                    self.intermediate.remove()
                    self.intermediate = None
                    self.intermediate_extra.remove()
                    return

                # ...the previous was *not* intermediate, creata a new label
                elif self.intermediate is None:
                    label = Label()
                    self.intermediate = label
                    self.intermediate_extra.update(progress = 0, total = None)
                    task_container.mount(self.intermediate)
                    task_container.mount(self.intermediate_extra)

                # ...the previous was intermediate too, reuse its label
                else:
                    label = self.intermediate

            # If the new status is *not* intermediate, but the previous status *was* intermediate, reuse its label
            elif self.intermediate:
                label = self.intermediate
                self.intermediate = None
                self.intermediate_extra.remove()

            # If the new status is *not* intermediate, and the previous wasn't either, create a new label
            else:
                label = Label()
                task_container.mount(label)

            # Resolve dictionary-based status updates
            if isinstance(status, dict):

                if status.get('info') == 'enter':
                    #label.update('Task has begun')
                    #self.update_intermediate_extra(task_container, status = status, intermediate = intermediate)
                    return

                if status.get('info') == 'start':
                    if status['pickup'] or status['first_stage']:
                        label.update(f'Picking up from: {status["pickup"]} ({status["first_stage"] or "copy"})')
                    else:
                        label.update('Starting from scratch')
                    return

                if status.get('info') == 'process':
                    label.update(f'[bold]({status["step"] + 1}/{status["step_count"]})[/bold] Processing input: {status["input"]}')
                    label.add_class('status-process')
                    return

                if status.get('info') == 'start-stage':
                    label.update(f'Starting stage: {status["stage"]}')
                    return

                if status.get('info') == 'storing':
                    label.update(f'Storing results...')
                    return

                if status.get('info') == 'completed':
                    label.update(f'Results have been stored')
                    label.add_class('status-success')
                    self.finished_tasks.add(status['task'])
                    self.update_task_state(task_id = self.task_ids[status['task']])
                    return

                if status.get('info') == 'error':
                    label.update('An error occurred:')
                    label.add_class('status-error')
                    task_container.mount(Label(status['traceback']))
                    return

                if status.get('info') == 'progress':
                    label.update(str(status.get('details')))
                    self.intermediate_extra.update(progress = status['step'], total = status['max_steps'])
                    return
                
                if status.get('info') == 'interrupted':
                    label.update('Batch run interrupted')
                    label.add_class('status-error')
                    return

            # Handle all remaining status updates
            label.update(str(status))

        except:
            log('StatusReader.handle_new_status', error = traceback.format_exc())
            raise