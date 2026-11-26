from pricing import calc
from orders import order_total
from report import summary

if __name__ == "__main__":
    print(calc([1.5, 2.5]))
    print(order_total([(2, 3.0), (1, 4.5)]))
    print(summary([10, 20]))
