# -*- coding: utf-8 -*-
"""第二题（1）：获取文件后缀名"""
def get_suffix(filename, ignore_dot=True):
    pos = filename.rfind('.')
    if pos <= 0:
        return ''
    return filename[pos + 1:] if ignore_dot else filename[pos:]

print(get_suffix('readme.txt'))
print(get_suffix('readme.txt.md'))
print(get_suffix('.readme'))
