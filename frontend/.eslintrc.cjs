/**
 * ESLint 8, legacy "eslintrc" format: the `lint` script's `--ext` flag only exists
 * there. Uses exactly the plugins already in devDependencies.
 */
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
  settings: { react: { version: 'detect' } },
  // No 'plugin:react/jsx-runtime': the codebase imports React explicitly in every
  // JSX file, and that preset would report each of those imports as unused.
  extends: [
    'eslint:recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
  ],
  plugins: ['react-refresh'],
  ignorePatterns: ['dist', 'node_modules', '.eslintrc.cjs'],
  rules: {
    // The codebase does not use PropTypes; component contracts are covered by tests.
    'react/prop-types': 'off',
    'react-refresh/only-export-components': [
      'warn',
      {
        allowConstantExport: true,
        // Deliberately co-located with the component they serve: each hook
        // beside its provider/dialog, the nav table beside the nav.
        allowExportNames: ['useDashboard', 'useCommandPalette', 'isMac', 'PAGES'],
      },
    ],
  },
  overrides: [
    {
      // Test files and build configs run under Node.
      files: ['tests/**/*.{js,jsx}', '*.config.js'],
      env: { node: true },
    },
  ],
}
