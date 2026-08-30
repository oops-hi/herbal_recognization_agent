# -*- coding: utf-8 -*-
"""第二题（2）：抽象基类与员工月薪结算"""
from abc import ABCMeta, abstractmethod


class Employee(metaclass=ABCMeta):
    """员工"""
    def __init__(self, name):
        self.name = name

    @abstractmethod
    def get_salary(self):
        """结算月薪"""
        pass


class Staff(Employee):
    def __init__(self, name, monthly_salary):
        super().__init__(name)
        self.monthly_salary = monthly_salary

    def get_salary(self):
        return self.monthly_salary


employee = Staff("张三", 8000)
print(f"员工: {employee.name}")
print(f"月薪: {employee.get_salary()}元")

months = 3
total_salary = employee.get_salary() * months
print(f"{months}个月总薪资: {total_salary}元")
