# -*- coding: utf-8 -*-
"""第五题：剪刀石头布游戏"""
import random


def rps_game(user):
    """模拟剪刀石头布：user 为用户输入，返回胜负提示"""
    choices = ['剪刀', '石头', '布']
    comp = random.choice(choices)          # 电脑随机出
    print(f'你出:{user}，电脑出:{comp}')

    if user == comp:                       # 相同 → 再来一次
        return '再来一次！'

    win = {'石头': '剪刀', '剪刀': '布', '布': '石头'}  # 谁赢谁
    if win[user] == comp:
        return '恭喜你，你赢了！'
    return '不好意思，你输了！'


if __name__ == '__main__':
    while True:
        user = input('请输入（剪刀/石头/布，q退出）：')
        if user == 'q':
            break
        if user not in ('剪刀', '石头', '布'):
            print('输入无效，请重新输入')
            continue
        print(rps_game(user))
