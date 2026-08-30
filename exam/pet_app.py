# -*- coding: utf-8 -*-
"""第七题（2）：宠物领养平台 —— 登录界面 + 主界面（Streamlit）"""
import streamlit as st
from datetime import datetime

st.set_page_config(page_title='宠物领养平台', page_icon='🐾')

# 初始化登录状态
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    # ========== 登录界面 ==========
    st.title('登录界面')
    username = st.text_input('用户名')
    password = st.text_input('密码', type='password')

    if st.button('登录'):
        if username == 'admin' and password == '123456':
            st.session_state.logged_in = True
            st.rerun()                      # 刷新，进入主界面
        else:
            st.error('用户名或密码错误！')
else:
    # ========== 主界面 ==========
    st.title('宠物领养平台')
    st.write(f'用户名：admin')
    st.write(f'登录日期：{datetime.now().strftime("%Y-%m-%d %H:%M")}')

    if st.button('登出'):
        st.session_state.logged_in = False
        st.rerun()                          # 返回登录界面
