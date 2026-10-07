from aiogram.fsm.state import State, StatesGroup


class KnowledgeStates(StatesGroup):
    active = State()


class TicketStates(StatesGroup):
    choosing_category = State()
    entering_text = State()


class UploadStates(StatesGroup):
    waiting_file = State()
    waiting_topic = State()
    waiting_type = State()


class AddFileStates(StatesGroup):
    waiting_file = State()
