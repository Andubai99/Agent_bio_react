# Agent_bio_react Desktop App

`desktop_app/` 是本项目的桌面软件前端目录，独立于 Python Agent 主逻辑。当前版本是 Electron + Vite + React + TypeScript 原型，启动后打开独立软件窗口，不需要浏览器。

## Commands

```powershell
npm install
npm run start:desktop
npm run build
```

## Scope

- 任务首页：左侧任务列表，勾选后进入待运行任务集合。
- 实时窗口：右侧展示当前任务窗口与执行步骤状态。
- 模型设置：支持切换推理模型、视觉模型，以及自定义 API Key、Endpoint、Model 等配置。

后续接入 Python 后端时，建议在 `desktop_app/src/` 下新增 `api/` 层，统一封装任务列表、运行状态、截图流和模型配置接口。
