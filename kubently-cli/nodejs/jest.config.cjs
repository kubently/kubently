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
  },
};
