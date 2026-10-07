from aiogram.fsm.state import State, StatesGroup


class QualifyStates(StatesGroup):
    step = State()  # номер шага — в данных FSM


class LeadStates(StatesGroup):
    name = State()
    phone = State()


class UploadStates(StatesGroup):
    waiting_file = State()
    waiting_key = State()
    waiting_topic = State()


class AddFileStates(StatesGroup):
    waiting_file = State()
