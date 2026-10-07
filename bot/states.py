from aiogram.fsm.state import State, StatesGroup


class TicketStates(StatesGroup):
    choosing_category = State()
    entering_text = State()


class UploadStates(StatesGroup):
    waiting_file = State()
    waiting_key = State()
    waiting_topic = State()


class AddFileStates(StatesGroup):
    waiting_file = State()
