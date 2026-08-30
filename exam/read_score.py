# -*- coding: utf-8 -*-
"""第四题：读取 test01.csv 并统计各科总分/平均分/最高分"""
import csv


def print_table(rows):
    """以整齐的表格形式打印数据"""
    headers = rows[0]
    # 按每列最宽内容对齐
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    print('  '.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print('-' * (sum(widths) + 2 * (len(headers) - 1)))
    for row in rows[1:]:
        print('  '.join(row[i].ljust(widths[i]) for i in range(len(headers))))


def main():
    # 1. 读取并打印原始表格（5分）
    with open('test01.csv', encoding='utf-8') as f:
        rows = list(csv.reader(f))
    print_table(rows)

    # 2. 各科目统计：总分、平均分（保留2位小数）、最高分（5分）
    headers = rows[0]          # ['姓名', '语文', '数学', '英语']
    data = rows[1:]            # 去掉表头
    print()
    for subj in headers[1:]:   # 跳过"姓名"列
        scores = [int(row[headers.index(subj)]) for row in data]
        total = sum(scores)
        avg = total / len(scores)
        print(f'{subj}: 总分={total}, 平均分={avg:.2f}, 最高分={max(scores)}')


if __name__ == '__main__':
    main()
