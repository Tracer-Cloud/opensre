# OpenSRE Documentation

This directory contains the source for the OpenSRE documentation, powered by [Mintlify](https://mintlify.com).

## Local Development

To preview the documentation locally, you need to install the Mintlify CLI.

### Prerequisites

- [Node.js](https://nodejs.org/) (version 18 or higher)
- [npm](https://www.npmjs.com/)

### Setup

1. Install the Mintlify CLI globally:

   ```bash
   npm i -g mint
   ```

2. Run the development server from this directory:

   ```bash
   mint dev
   ```

   Alternatively, from the project root, you can run:

   ```bash
   make docs-dev
   ```

The documentation will be available at `http://localhost:3000`.

## Structure

- `docs.json`: Configuration file for the documentation, including navigation and theme settings.
- `*.mdx`: Documentation pages written in MDX (Markdown with JSX).
- `images/`: Static assets such as screenshots and diagrams.
- `snippets/`: Reusable content blocks.

## Contributing

1. Create a new branch for your changes.
2. Edit the relevant `.mdx` files or update `docs.json` if adding new pages.
3. Preview your changes locally using `mint dev`.
4. Submit a Pull Request with a clear description of your changes.

## Resources

- [Mintlify Documentation](https://mintlify.com/docs)
- [MDX Syntax Guide](https://mdxjs.com/docs/what-is-mdx/)
