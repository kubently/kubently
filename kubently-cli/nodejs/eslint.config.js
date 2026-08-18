// Flat config (ESLint 9+). The repo declared `npm run lint` and carried the
// eslint + @typescript-eslint dependencies, but shipped no config file at all,
// so linting has never actually run — `eslint src --ext .ts` exited with
// "couldn't find an eslint.config.js". This is the minimum that makes the
// script real, and therefore makes an eslint version bump verifiable.
import tsParser from '@typescript-eslint/parser';
import tsPlugin from '@typescript-eslint/eslint-plugin';

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },
  {
    files: ['src/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { impliedStrict: true },
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
    },
    rules: {
      // Deliberately narrow: correctness rules only. Style is prettier's job
      // (`npm run format`), and a broad ruleset here would bury the signal
      // under a backlog nobody triages.
      'no-undef': 'off', // TypeScript already checks this, and knows the DOM/node globals
      'no-unused-vars': 'off', // superseded by the TS-aware rule below
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-constant-condition': ['error', { checkLoops: false }],
      'no-dupe-keys': 'error',
      'no-unreachable': 'error',
      'require-atomic-updates': 'off',
    },
  },
];
