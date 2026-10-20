"""錯誤型別，對應 CLI 結束碼。"""


class InputError(Exception):
    """輸入資料錯誤（結束碼 1）。"""


class UsageError(Exception):
    """使用方式錯誤（結束碼 2）。"""
