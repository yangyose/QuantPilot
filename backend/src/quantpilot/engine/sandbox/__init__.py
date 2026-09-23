"""C5 策略插件沙箱（SDD §15.2）。受限子进程执行用户插件，Engine 层无 IO 规约的例外由
`plugin_runner` 自己守住：它只 spawn 子进程 + 传 pandas 结构，不碰 DB / 文件 / 网络。"""
