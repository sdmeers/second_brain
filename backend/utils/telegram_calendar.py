import calendar
from datetime import datetime

def generate_telegram_calendar(year: int, month: int, callback_prefix: str = "cal") -> dict:
    """Generates an inline keyboard calendar for any year and month.
    Each day button has callback_data in format: '{callback_prefix}:pick:{YYYY-MM-DD}:{DD-MM-YYYY}'
    """
    month_name = datetime(year, month, 1).strftime("%B %Y")
    
    keyboard = []
    # Header: Month & Year + Nav arrows
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    keyboard.append([
        {"text": "◀", "callback_data": f"{callback_prefix}:nav:{prev_year}:{prev_month}"},
        {"text": month_name, "callback_data": f"{callback_prefix}:ignore"},
        {"text": "▶", "callback_data": f"{callback_prefix}:nav:{next_year}:{next_month}"}
    ])
    
    # Weekday header
    keyboard.append([
        {"text": "Mo", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "Tu", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "We", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "Th", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "Fr", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "Sa", "callback_data": f"{callback_prefix}:ignore"},
        {"text": "Su", "callback_data": f"{callback_prefix}:ignore"}
    ])
    
    # Days grid
    month_cal = calendar.monthcalendar(year, month)
    for week in month_cal:
        row = []
        for day in week:
            if day == 0:
                row.append({"text": " ", "callback_data": f"{callback_prefix}:ignore"})
            else:
                date_str = f"{day:02d}-{month:02d}-{year}"
                iso_str = f"{year}-{month:02d}-{day:02d}"
                row.append({"text": str(day), "callback_data": f"{callback_prefix}:pick:{iso_str}:{date_str}"})
        keyboard.append(row)
        
    return {"inline_keyboard": keyboard}
