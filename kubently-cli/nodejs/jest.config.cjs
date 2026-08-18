/** ts-jest transpiles TS tests to CJS; moduleNameMapper strips the ESM ".js"
 * suffix this codebase uses on relative imports so jest can resolve the .ts source. */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  moduleNameMapper: {
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  transform: {
    '^.+\\.ts$': ['ts-jest', { tsconfig: { module: 'commonjs' } }],
    // Several runtime deps (uuid, chalk, ora, open, inquirer and their helpers)
    // ship ESM only. Without a .js transform they reach the CJS test runtime as
    // raw `export` statements and every module that imports them fails to load —
    // which is why the CLI had almost no tests despite 21 source files.
    '^.+\\.js$': ['ts-jest', { tsconfig: { module: 'commonjs', allowJs: true } }],
  },
  // Default is to skip node_modules entirely; the negative lookahead opts the
  // ESM-only packages back in so they get transpiled to CJS like our own code.
  transformIgnorePatterns: [
    `/node_modules/(?!(${[
      'uuid',
      'chalk',
      'ora',
      'inquirer',
      '@inquirer',
      'ansi-regex',
      'ansi-styles',
      'cli-cursor',
      'cli-spinners',
      'emoji-regex',
      'get-east-asian-width',
      'is-interactive',
      'is-unicode-supported',
      'log-symbols',
      'mimic-function',
      'onetime',
      'restore-cursor',
      'signal-exit',
      'stdin-discarder',
      'string-width',
      'strip-ansi',
    ].join('|')})/)`,
  ],
};
