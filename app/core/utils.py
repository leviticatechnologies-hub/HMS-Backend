"""
Utility functions for common operations across the application.
"""
import secrets
import string
from datetime import datetime, time, timezone
from typing import Optional, Union


def ensure_datetime_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize a datetime for API output and SQL binds against timestamptz columns.
    PostgreSQL/asyncpg often return timezone-aware values; naive Python datetimes (e.g. from
    strptime) mixed with those can raise: TypeError: can't compare offset-naive and offset-aware datetimes.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_barcode_png_bytes(barcode_value: str) -> Optional[bytes]:
    """
    Generate CODE128 barcode as PNG bytes (in-memory, no file save).
    
    Requires: pip install "python-barcode[images]"
    
    Args:
        barcode_value: Text to encode in barcode (e.g. sample barcode value)
        
    Returns:
        PNG bytes, or None if generation fails (missing deps)
    """
    try:
        from io import BytesIO
        import barcode
        from barcode.writer import ImageWriter

        value = (barcode_value or "0")[:80]
        code128 = barcode.get_barcode_class("code128")
        bc = code128(value, writer=ImageWriter())
        buffer = BytesIO()
        bc.write(buffer)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception:
        return None


def generate_appointment_ref() -> str:
    """
    Generate a human-readable appointment reference.
    
    Format: APT-XXXX-### (e.g., APT-CARD-123, APT-ORTH-456)
    
    Returns:
        str: Human-readable appointment reference
    """
    import random
    
    # Medical department codes for readability
    dept_codes = ['CARD', 'ORTH', 'NEUR', 'PEDI', 'GYNE', 'DERM', 'ENDO', 'GAST', 'PULM', 'ONCO']
    
    # Random department code
    dept_code = random.choice(dept_codes)
    
    # Random 3-digit number
    number = random.randint(100, 999)
    
    return f"APT-{dept_code}-{number}"


def generate_patient_ref() -> str:
    """
    Generate a human-readable patient reference.
    
    Format: PAT-XXXX-### (e.g., PAT-JOHN-123, PAT-MARY-456)
    
    Returns:
        str: Human-readable patient reference
    """
    import random
    
    # Common name codes for readability
    name_codes = ['JOHN', 'MARY', 'ALEX', 'SARA', 'MIKE', 'ANNA', 'DAVE', 'LUCY', 'RYAN', 'EMMA']
    
    # Random name code
    name_code = random.choice(name_codes)
    
    # Random 3-digit number
    number = random.randint(100, 999)
    
    return f"PAT-{name_code}-{number}"


def parse_date_string(date_str: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """
    Parse date string in various formats to datetime object.
    
    Supports:
    - ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
    - DD-MM-YYYY format
    - MM-DD-YYYY format
    - DD/MM/YYYY format
    - MM/DD/YYYY format
    - Natural formats: "June 7 2003", "7 June 2003", "June 7, 2003"
    - Month name formats: "Jun 7 2003", "7 Jun 2003"
    
    Args:
        date_str: Date string or datetime object
        
    Returns:
        datetime object or None if date_str is None
        
    Raises:
        ValueError: If date string format is not recognized
    """
    if not date_str:
        return None
    
    # If it's already a datetime object, return as is
    if isinstance(date_str, datetime):
        return date_str
    
    # Clean up the date string
    date_str = str(date_str).strip()
    
    # Try ISO format first (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        pass
    
    # Try DD-MM-YYYY format
    try:
        return datetime.strptime(date_str, '%d-%m-%Y')
    except ValueError:
        pass
    
    # Try MM-DD-YYYY format
    try:
        return datetime.strptime(date_str, '%m-%d-%Y')
    except ValueError:
        pass
    
    # Try DD/MM/YYYY format
    try:
        return datetime.strptime(date_str, '%d/%m/%Y')
    except ValueError:
        pass
    
    # Try MM/DD/YYYY format
    try:
        return datetime.strptime(date_str, '%m/%d/%Y')
    except ValueError:
        pass
    
    # Try natural formats with full month names
    natural_formats = [
        '%B %d %Y',      # "June 7 2003"
        '%d %B %Y',      # "7 June 2003"
        '%B %d, %Y',     # "June 7, 2003"
        '%d %B, %Y',     # "7 June, 2003"
        '%b %d %Y',      # "Jun 7 2003"
        '%d %b %Y',      # "7 Jun 2003"
        '%b %d, %Y',     # "Jun 7, 2003"
        '%d %b, %Y',     # "7 Jun, 2003"
    ]
    
    for fmt in natural_formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    # If all formats fail, raise error with helpful message
    raise ValueError(
        f"Invalid date format: '{date_str}'. "
        f"Supported formats: YYYY-MM-DD, DD-MM-YYYY, MM-DD-YYYY, DD/MM/YYYY, MM/DD/YYYY, "
        f"'June 7 2003', '7 June 2003', 'Jun 7 2003', etc."
    )


# Alias for billing module compatibility
parse_date_flexible = parse_date_string


def format_date_iso(date_obj: Optional[datetime]) -> Optional[str]:
    """
    Format datetime object to ISO string (YYYY-MM-DD).
    
    Args:
        date_obj: datetime object
        
    Returns:
        ISO formatted date string or None if date_obj is None
    """
    if not date_obj:
        return None
    
    return date_obj.strftime('%Y-%m-%d')


def format_datetime_iso(datetime_obj: Optional[datetime]) -> Optional[str]:
    """
    Format datetime object to ISO string (YYYY-MM-DDTHH:MM:SS).
    
    Args:
        datetime_obj: datetime object
        
    Returns:
        ISO formatted datetime string or None if datetime_obj is None
    """
    if not datetime_obj:
        return None
    
    return datetime_obj.isoformat()


def parse_time_string(time_str: str) -> time:
    """
    Parse time string in various formats to time object.
    
    Supported formats:
    - HH:MM (24-hour format)
    - HH:MM:SS (24-hour format with seconds)
    - H:MM (single digit hour)
    
    Args:
        time_str: Time string to parse
        
    Returns:
        time: Parsed time object
        
    Raises:
        ValueError: If time string format is invalid
    """
    if not time_str:
        raise ValueError("Time string cannot be empty")
    
    # Remove any whitespace
    time_str = time_str.strip()
    
    # Try different time formats
    time_formats = [
        "%H:%M",      # 14:30
        "%H:%M:%S",   # 14:30:00
        "%I:%M %p",   # 2:30 PM
        "%I:%M:%S %p" # 2:30:00 PM
    ]
    
    for fmt in time_formats:
        try:
            parsed_time = datetime.strptime(time_str, fmt).time()
            return parsed_time
        except ValueError:
            continue
    
    # If no format worked, raise error
    raise ValueError(f"Invalid time format: {time_str}. Expected formats: HH:MM, HH:MM:SS, H:MM AM/PM")


def generate_lab_order_number() -> str:
    """
    Generate a unique lab order number.
    
    Format: LAB-YYYY-NNNNN (e.g., LAB-2026-00045)
    
    Returns:
        str: Lab order number
    """
    import random
    
    current_year = datetime.now().year
    # Generate random 5-digit number for demo purposes
    # In production, this would be sequential based on database
    sequence = random.randint(1, 99999)
    
    return f"LAB-{current_year}-{sequence:05d}"


def generate_sample_number() -> str:
    """
    Generate a unique sample number.
    
    Format: SMP-YYYY-NNNNN (e.g., SMP-2026-00023)
    
    Returns:
        str: Sample number
    """
    import random
    
    current_year = datetime.now().year
    # Generate random 5-digit number for demo purposes
    # In production, this would be sequential based on database
    sequence = random.randint(1, 99999)
    
    return f"SMP-{current_year}-{sequence:05d}"


def generate_sample_barcode(lab_order_no: str, sample_sequence: int) -> str:
    """
    Generate a unique barcode for a sample.
    
    Format: LAB-ORD-{order_no}-SMP-{seq} (e.g., LAB-ORD-LAB-2026-00045-SMP-1)
    
    Args:
        lab_order_no: Lab order number
        sample_sequence: Sequence number for this sample within the order
        
    Returns:
        str: Unique barcode value
    """
    return f"LAB-ORD-{lab_order_no}-SMP-{sample_sequence}"


def generate_sample_number() -> str:
    """
    Generate a unique sample number.
    
    Format: SMP-YYYY-NNNNN (e.g., SMP-2026-00023)
    
    Returns:
        str: Sample number
    """
    import random
    
    current_year = datetime.now().year
    # Generate random 5-digit number for demo purposes
    # In production, this would be sequential based on database
    sequence = random.randint(1, 99999)
    
    return f"SMP-{current_year}-{sequence:05d}"


def generate_sample_barcode(lab_order_no: str, sample_sequence: int) -> str:
    """
    Generate a unique barcode for a sample.
    
    Format: LAB-ORD-{order_no}-SMP-{sequence} (e.g., LAB-ORD-LAB-2026-00045-SMP-1)
    
    Args:
        lab_order_no: Lab order number
        sample_sequence: Sequential number for this sample in the order
        
    Returns:
        str: Barcode value
    """
    return f"LAB-ORD-{lab_order_no}-SMP-{sample_sequence}"


def validate_medicine_id(medicine_id: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Validate medicine ID format and return validation result.
    
    Args:
        medicine_id: Medicine ID string to validate
        
    Returns:
        tuple: (is_valid, error_message)
            - is_valid: True if valid UUID format or None/empty
            - error_message: Error message if invalid, None if valid
    """
    import uuid
    
    # Allow None or empty strings
    if not medicine_id or not medicine_id.strip():
        return True, None
    
    try:
        # Try to parse as UUID
        uuid.UUID(medicine_id.strip())
        return True, None
    except ValueError:
        return False, f"Invalid medicine ID format: '{medicine_id}'. Expected UUID format."


def sanitize_medicine_id(medicine_id: Optional[str]) -> Optional[str]:
    """
    Sanitize medicine ID by stripping whitespace and validating format.
    
    Args:
        medicine_id: Medicine ID string to sanitize
        
    Returns:
        Sanitized medicine ID or None if invalid/empty
        
    Raises:
        ValueError: If medicine ID format is invalid
    """
    if not medicine_id or not medicine_id.strip():
        return None
    
    sanitized_id = medicine_id.strip()
    is_valid, error_message = validate_medicine_id(sanitized_id)
    
    if not is_valid:
        raise ValueError(error_message)
    
    return sanitized_id



def resolve_user_id(user_id: Optional[Union[str, int]]) -> Optional[str]:
    """
    Resolve user ID to string format.
    
    Args:
        user_id: User ID as string, int, or None
        
    Returns:
        User ID as string or None
    """
    if user_id is None:
        return None
    return str(user_id)


def absolute_public_asset_url(path: Optional[str]) -> Optional[str]:
    """
    Turn stored relative paths like `/uploads/...` into absolute URLs using `settings.APP_PUBLIC_URL`.

    SPAs (e.g. Vite on :3000) request `<img src="/uploads/...">` on the wrong origin and get 404.
    Prefixing with the API public base fixes display while keeping DB values portable.
    """
    if path is None:
        return None
    s = str(path).strip()
    if not s:
        return None
    if s.startswith(("http://", "https://", "//")):
        return s
    from app.core.config import settings

    base = (getattr(settings, "APP_PUBLIC_URL", None) or "").strip().rstrip("/")
    if not base:
        return s
    if not s.startswith("/"):
        s = "/" + s
    return f"{base}{s}"
