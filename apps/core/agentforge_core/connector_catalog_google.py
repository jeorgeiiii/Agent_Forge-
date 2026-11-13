"""Google integrations - connected with "Sign in with Google" (OAuth), not pasted keys.

The person picks their Google account once on the Integrations page; the vault stores the tokens
encrypted and refreshes them automatically. Scopes are kept small:
  Sheets: spreadsheets (read/write the sheets the account can open)
  Drive:  drive.readonly (search/read) + drive.file (create folders/files made by AgentForge)
Note: drive.readonly is a Google "restricted" scope - fine for testing with your own test users,
but a public launch needs Google's app verification (see the integrations policy notes).
"""

from .connectors import Connector, ConnectorAction as A, ConnectorField as F

_SHEET_ID = F("spreadsheet_id", "Spreadsheet ID", required=True,
              help="From the sheet's address: docs.google.com/spreadsheets/d/<THIS PART>/edit")
_RANGE = F("range", "Sheet and cells", required=True,
           help="A tab name like Sheet1, or cells like Sheet1!A1:D50")
_ROWS = F("rows", "Rows", "rows", required=True,
          help='A list of rows, e.g. [["Ada", "ada@example.com", "{{ $input.score }}"]]')

GOOGLE_SHEETS = Connector(
    id="google_sheets", name="Google Sheets", category="Productivity",
    description="Read rows from a Google Sheet and add or update rows.",
    base_url="https://sheets.googleapis.com/v4", key_label="Google account",
    oauth_provider="google", oauth_scopes=("https://www.googleapis.com/auth/spreadsheets",),
    key_help="Click 'Sign in with Google' and choose the account that can open your sheets.",
    docs_url="https://developers.google.com/sheets/api/reference/rest",
    actions=(
        A("read_rows", "Read rows", "All rows of a tab or a cell range.", "GET",
          "/spreadsheets/{spreadsheet_id}/values/{range}", fields=(_SHEET_ID, _RANGE), result="values"),
        A("append_rows", "Add rows", "Add rows under the last filled row.", "POST",
          "/spreadsheets/{spreadsheet_id}/values/{range}:append", fields=(_SHEET_ID, _RANGE, _ROWS),
          const_query={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
          body={"values": "rows"}, result="updates", writes=True),
        A("update_range", "Update cells", "Overwrite a range of cells.", "PUT",
          "/spreadsheets/{spreadsheet_id}/values/{range}", fields=(_SHEET_ID, _RANGE, _ROWS),
          const_query={"valueInputOption": "USER_ENTERED"}, body={"values": "rows"}, writes=True),
        A("create_spreadsheet", "Create spreadsheet", "Make a new, empty spreadsheet.", "POST", "/spreadsheets",
          fields=(F("title", "Title", required=True),), body={"properties.title": "title"}, writes=True),
    ),
)

GOOGLE_DRIVE = Connector(
    id="google_drive", name="Google Drive", category="Documents",
    description="Find files in Google Drive, read Google Docs as text and create folders.",
    base_url="https://www.googleapis.com/drive/v3", key_label="Google account",
    oauth_provider="google",
    oauth_scopes=("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"),
    key_help="Click 'Sign in with Google' and choose the account whose Drive you want to use.",
    docs_url="https://developers.google.com/drive/api/reference/rest/v3",
    actions=(
        A("search_files", "Find files", "Files matching a search.", "GET", "/files",
          fields=(F("query", "Search", help="e.g. name contains 'report'  (leave empty for recent files)"),
                  F("limit", "How many", "integer")),
          query={"q": "query", "pageSize": "limit"},
          const_query={"fields": "files(id,name,mimeType,modifiedTime,webViewLink)", "orderBy": "modifiedTime desc"},
          result="files"),
        A("get_file", "File details", "Name, type, size and link of one file.", "GET", "/files/{file_id}",
          fields=(F("file_id", "File ID", required=True, help="From the file's link: …/d/<THIS PART>/…"),),
          const_query={"fields": "id,name,mimeType,size,modifiedTime,webViewLink"}),
        A("read_doc_text", "Read a Google Doc", "The text of a Google Doc.", "GET", "/files/{file_id}/export",
          fields=(F("file_id", "Doc ID", required=True, help="From the doc's link: …/document/d/<THIS PART>/edit"),),
          const_query={"mimeType": "text/plain"}),
        A("create_folder", "Create folder", "Make a new folder in My Drive.", "POST", "/files",
          fields=(F("name", "Folder name", required=True),), body={"name": "name"},
          const_body={"mimeType": "application/vnd.google-apps.folder"}, writes=True),
    ),
)

GOOGLE_CONNECTORS = (GOOGLE_SHEETS, GOOGLE_DRIVE)
