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
    // Most of this CLI's runtime dependencies (commander, uuid, chalk, ora,
    // inquirer and their transitive helpers) are ESM-only, so without a .js
    // transform they reach the CJS test runtime as raw `export`/`import`
    // statements and every module importing them fails to load. That is most of
    // why the CLI had almost no tests despite 21 source files.
    '^.+\\.js$': ['ts-jest', { tsconfig: { module: 'commonjs', allowJs: true } }],
  },
  // Inverted on purpose: transform everything in node_modules rather than
  // maintaining an allow-list. The ESM dependency graph under inquirer/ora runs
  // several packages deep (@inquirer/core -> fast-wrap-ansi -> fast-string-width
  // -> ...) and every dependency bump reshuffles it, so an allow-list turns each
  // bump into a round of whack-a-mole. Only modules a test actually requires get
  // transformed, and results are cached.
  //
  // `open` is the one exclusion: it reads import.meta.url, which has no CJS
  // equivalent, so tests that reach it mock the module instead.
  transformIgnorePatterns: ['/node_modules/open/'],
};
