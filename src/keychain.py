"""登录钥匙串里的签名密码：默认只允许创建它的那个可执行文件读取。

为什么不用 `/usr/bin/security`：钥匙串 ACL 校验的是**调用进程**的签名，
不是父进程的。把 `/usr/bin/security` 写进信任列表，等于对任何能执行它的
程序放行——保护等于零；一个都不写，则每次读取都弹系统确认，后台作业里
没人点就会一直等下去。已实测这两种结果，所以这里直接调用 Security 框架，
用系统默认的 ACL：创建者受信任，别的程序读取要用户确认。

前提：助手最好用固定的本地身份签名，指定要求才是
`identifier … and certificate root = H"…"`，重新构建不变。ad-hoc 的指定
要求是 cdhash，重新构建就变，ACL 随之失效，用户会被要求再确认一次。
这一点写进 docs/SIGNING_AND_PERMISSIONS.md，不在这里假装不存在。
"""
import ctypes

FRAMEWORK = '/System/Library/Frameworks/Security.framework/Security'

ERR_SEC_SUCCESS = 0
ERR_SEC_ITEM_NOT_FOUND = -25300
ERR_SEC_AUTH_FAILED = -25293
ERR_SEC_USER_CANCELED = -128

# 读取时可能等用户点系统对话框。这些调用不能设超时（ctypes 里没有安全的
# 中断方式），所以只在用户在场、由界面发起的签名路径上调用。
_library = None


def library():
    """惰性加载 Security 框架；非 macOS 或加载失败时抛出，由调用方降级。"""
    global _library
    if _library is None:
        lib = ctypes.CDLL(FRAMEWORK)
        lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        lib.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        lib.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p)]
        lib.SecKeychainItemFreeContent.restype = ctypes.c_int32
        lib.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.SecKeychainItemDelete.restype = ctypes.c_int32
        lib.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        _library = lib
    return _library


def _find(lib, service, account, with_data):
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account,
        ctypes.byref(length) if with_data else None,
        ctypes.byref(data) if with_data else None,
        ctypes.byref(item))
    return status, length, data, item


def store(service, account, password):
    """写入或覆盖条目；返回系统状态码（0 表示成功）。

    条目由**本进程**创建，ACL 默认只信任本进程的可执行文件。不要改成
    先起一个命令行工具再让它写：那条路只能把信任指向某个路径下的程序，
    而真正发起访问的是那个工具本身，等于谁都读得到。
    """
    lib = library()
    delete(service, account)
    service = service.encode()
    account = account.encode()
    password = password.encode()
    item = ctypes.c_void_p()
    return lib.SecKeychainAddGenericPassword(
        None, len(service), service, len(account), account,
        len(password), password, ctypes.byref(item))


def read(service, account):
    """读取密码；取不到返回 None。

    条目存在但 ACL 不认本进程时，系统会先问用户，用户拒绝后返回
    `ERR_SEC_AUTH_FAILED`。这一调用可能阻塞到用户作答，只在前台路径用。
    """
    lib = library()
    service = service.encode()
    account = account.encode()
    status, length, data, _ = _find(lib, service, account, True)
    if status != ERR_SEC_SUCCESS or not data:
        return None
    try:
        return ctypes.string_at(data, length.value).decode()
    finally:
        lib.SecKeychainItemFreeContent(None, data)


def exists(service, account):
    """条目是否存在；只查属性，不读密码，所以不会触发确认框。"""
    lib = library()
    service = service.encode()
    account = account.encode()
    status, _, _, item = _find(lib, service, account, False)
    return status == ERR_SEC_SUCCESS and bool(item)


def delete(service, account):
    """删除条目；不存在时返回 False。"""
    lib = library()
    service = service.encode()
    account = account.encode()
    status, _, _, item = _find(lib, service, account, False)
    if status != ERR_SEC_SUCCESS or not item:
        return False
    return lib.SecKeychainItemDelete(item) == ERR_SEC_SUCCESS
