def quick_sort(arr):
    """
    快速排序算法
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(log n) 递归调用栈
    """
    if len(arr) <= 1:
        return arr
    
    # 选择基准值（这里选择中间元素）
    pivot = arr[len(arr) // 2]
    
    # 分区：小于基准、等于基准、大于基准
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    # 递归排序并合并
    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """
    原地快速排序（不占用额外空间）
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区并获取基准位置
        pivot_index = partition(arr, low, high)
        
        # 递归排序左右两部分
        quick_sort_inplace(arr, low, pivot_index - 1)
        quick_sort_inplace(arr, pivot_index + 1, high)
    
    return arr


def partition(arr, low, high):
    """
    分区函数：将小于基准的元素放左边，大于的放右边
    """
    pivot = arr[high]  # 选择最后一个元素作为基准
    i = low - 1
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]  # 交换
    
    # 将基准放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


# 测试代码
if __name__ == "__main__":
    # 测试数据
    test_arr1 = [64, 34, 25, 12, 22, 11, 90]
    test_arr2 = [64, 34, 25, 12, 22, 11, 90]
    
    print("原始数组:", test_arr1)
    
    # 使用普通快速排序
    sorted_arr1 = quick_sort(test_arr1)
    print("快速排序结果:", sorted_arr1)
    
    # 使用原地快速排序
    quick_sort_inplace(test_arr2)
    print("原地排序结果:", test_arr2)
    
    # 更多测试
    print("\n其他测试:")
    print("空数组:", quick_sort([]))
    print("单元素:", quick_sort([5]))
    print("已排序:", quick_sort([1, 2, 3, 4, 5]))
    print("逆序:", quick_sort([5, 4, 3, 2, 1]))
    print("重复元素:", quick_sort([3, 1, 4, 1, 5, 9, 2, 6, 5]))