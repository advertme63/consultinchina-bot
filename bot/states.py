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
    title = State()
    description = State()
    sort_order = State()
    send_name = State()


class EditFileStates(StatesGroup):
    value = State()  # новое значение поля; id и поле — в данных FSM
    file = State()  # новый файл взамен


class RatingStates(StatesGroup):
    feedback = State()  # «Что не так?» после 👎; message_id — в данных FSM
