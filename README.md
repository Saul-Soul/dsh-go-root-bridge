# dsh-go-root-bridge · Root 桥接

> by [Koshca](https://github.com/saul-soul)

dsh-go 原生插件：在 Root 桥接面板实时查看内核提权状态，并提供一条基于 KernelSU
授权的「全盘文件桥」。适用于已 root（KernelSU / KowSU 等内核级方案）的设备。

## 功能

- **权限状态面板**：uid、内核授权钩子、reboot 通道、驱动 fd、Root 桥在线状态
- **自动提权**：利用 KernelSU 内核接口（reboot 超级调用 → 驱动 fd → `GRANT_ROOT`），
  在引擎容器内拿到真实 root 凭据（`u:r:ksu:s0`）
- **全盘文件桥**：root 守护进程通过 unix socket 提供全盘读写 / 列目录 / 执行，
  宿主路径用 `host:` 前缀穿透容器视图
- **自愈**：桥掉线后，面板「重新探测」会自动拉起守护进程（独立会话常驻）

## 前置条件

- 设备已安装内核级 root（KernelSU 系），且 **本 App 的 uid 已在管理器中授权**
- 授权与提权全部走内核 allowlist 校验，不需要也不修改任何 SELinux 策略

## 安装

```sh
dsh plugin --profile web add dsh-go-root-bridge
```

## 使用

1. 打开对话详情 → 插件 → 「权限状态」面板
2. 点「重新探测」：若桥未在线会自动提权拉起
3. 状态显示 `root ✓（桥接 uid 0）` 即全盘可读

## 安全说明

- 所有 root 操作经由 Agent 的工具审批层，本插件**不提供**无审批的 root 入口
- unix socket 权限 `0600`，仅限本机容器内访问
- 插件不读取、不上传任何密钥；探测日志只含权限元数据

## 免责声明

本插件以「已获得设备 root 授权」为前提，供设备所有者管理自己的设备。
请遵守当地法律法规，勿用于未授权设备。

## License

MIT
