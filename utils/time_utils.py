from datetime import datetime, time, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def parse_time(time_str: str) -> time:
    """Parse a time string like '20:00' into a datetime.time object."""
    t = datetime.strptime(time_str.strip(), "%H:%M")
    return t.time()

def is_time_in_window(current_time: time, start: time, end: time) -> bool:
    """
    Check if current_time is between start and end.
    Handles midnight crossovers (e.g., start 22:00, end 02:00).
    """
    if start < end:
        return start <= current_time < end
    else: # Crosses midnight
        return current_time >= start or current_time < end

def is_time_to_hard_exit(current_time: time, hard_exit: time, start: time) -> bool:
    """
    Check if we should trigger a hard exit.
    This is true if the current time is exactly at hard_exit or in the off-hours
    between hard_exit and the next start time.
    """
    return is_time_in_window(current_time, hard_exit, start)

