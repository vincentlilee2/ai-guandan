import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: { ...globals.browser, process: 'readonly' },
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // 允许 _ 与大写开头的占位/常量未使用；catch (_) 与未用形参同理
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        args: 'after-used',
        argsIgnorePattern: '^_',
        caughtErrors: 'all',
        caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
  {
    // 前端单测文件：使用 vitest 全局 + node，且不受 react-refresh 规则约束
    files: ['src/**/*.test.{js,jsx}', 'src/**/*.spec.{js,jsx}', 'src/test/**/*.js'],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
