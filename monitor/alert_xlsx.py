"""
Builds the Excel workbook attached to every media-digest alert email.

Shares the (online, print, social, broadcast) lists already gathered by
alert_email.gather() — one sheet per media type, same rows as the HTML digest.
"""
import io

import xlsxwriter

# (header, attribute, column width) per sheet. Missing/blank values render as ''.
_ONLINE_COLS = [
    ('Source', 'source', 28), ('Headline', 'headline', 50), ('Summary', 'summary', 50),
    ('URL', 'url', 40), ('Date Published', 'date_published', 14), ('Country', 'country', 14),
    ('Sentiment', 'sentiment', 12), ('AVE', 'ave', 12), ('Coverage', 'coverage', 14),
    ('Reach', 'reach', 12), ('Relevancy', 'relevancy', 12),
]
_PRINT_COLS = [
    ('Source', 'source', 28), ('Headline', 'headline', 50), ('Summary', 'summary', 50),
    ('Author', 'author', 20), ('Section', 'section', 16), ('URL', 'url', 40),
    ('Date Published', 'date_published', 14), ('Country', 'country', 14),
    ('Sentiment', 'sentiment', 12), ('AVE', 'ave', 12), ('Reach', 'reach', 12),
    ('Relevancy', 'relevancy', 12),
]
_SOCIAL_COLS = [
    ('Platform', 'platform', 14), ('Page Name', 'page_name', 24), ('Headline', 'headline', 50),
    ('Summary', 'summary', 50), ('URL', 'url', 40), ('Date Published', 'date_published', 14),
    ('Country', 'country', 14), ('Sentiment', 'sentiment', 12), ('AVE', 'ave', 12),
    ('Rank', 'rank', 10), ('Reach', 'reach', 12), ('Relevancy', 'relevancy', 12),
]
_BROADCAST_COLS = [
    ('Source', 'source', 28), ('Headline', 'headline', 50), ('Summary', 'summary', 50),
    ('URL', 'url', 40), ('Date Published', 'date_published', 14), ('Country', 'country', 14),
    ('Sentiment', 'sentiment', 12), ('AVE', 'ave', 12), ('Relevancy', 'relevancy', 12),
    ('Duration', 'duration', 12), ('Broadcast Type', 'broadcast_type', 16),
]

# Sheet name capped at 31 chars by the xlsx format; these are all well under it.
_SHEETS = [
    ('Online', _ONLINE_COLS),
    ('Print', _PRINT_COLS),
    ('Social', _SOCIAL_COLS),
    ('Broadcast', _BROADCAST_COLS),
]


def build_workbook(online, print_arts, social, broadcast):
    """
    Return the .xlsx file as bytes for the given (online, print, social,
    broadcast) record lists — same shape as alert_email.gather()'s return value.
    """
    lists = {'Online': online, 'Print': print_arts, 'Social': social, 'Broadcast': broadcast}

    buf = io.BytesIO()
    workbook = xlsxwriter.Workbook(buf, {'in_memory': True, 'default_date_format': 'yyyy-mm-dd'})
    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1})
    date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})

    for sheet_name, cols in _SHEETS:
        ws = workbook.add_worksheet(sheet_name)
        for col_idx, (header, _attr, width) in enumerate(cols):
            ws.write(0, col_idx, header, header_fmt)
            ws.set_column(col_idx, col_idx, width)
        ws.freeze_panes(1, 0)

        for row_idx, item in enumerate(lists[sheet_name], start=1):
            for col_idx, (_header, attr, _width) in enumerate(cols):
                value = getattr(item, attr, None)
                # Defensive throughout: one malformed field (bad CSV import, a
                # stray non-numeric value, an illegal XML character in scraped
                # text, an over-length cell) must never blow up the whole email
                # send — worst case that one cell falls back to a blank/string.
                if attr == 'date_published' and value:
                    try:
                        ws.write_datetime(row_idx, col_idx, value, date_fmt)
                    except (TypeError, ValueError):
                        ws.write_string(row_idx, col_idx, str(value))
                elif attr in ('ave', 'reach', 'rank', 'relevancy') and value is not None:
                    try:
                        ws.write_number(row_idx, col_idx, float(value))
                    except (TypeError, ValueError):
                        ws.write_string(row_idx, col_idx, str(value))
                else:
                    text = str(value) if value else ''
                    # Excel cells cap at 32,767 characters.
                    if len(text) > 32767:
                        text = text[:32764] + '...'
                    try:
                        ws.write_string(row_idx, col_idx, text)
                    except Exception:
                        # Illegal XML control characters etc. — strip to ASCII
                        # printable as a last resort rather than dropping the row.
                        ws.write_string(row_idx, col_idx, text.encode('ascii', 'ignore').decode())

    workbook.close()
    return buf.getvalue()
