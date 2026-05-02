const path = require("path");
const crypto = require("crypto");
const fs = require("fs");
const webpack = require("webpack");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

const devServerPort = Number(process.env.PORT || 3000);
const apiProxyTarget = process.env.SHOTWRIGHT_API_PROXY_TARGET || "http://127.0.0.1:8000";

function createStaticAssetPlugin(name, filename, source) {
  return {
    apply(compiler) {
      compiler.hooks.thisCompilation.tap(name, (compilation) => {
        compilation.hooks.processAssets.tap(
          {
            name,
            stage: compiler.webpack.Compilation.PROCESS_ASSETS_STAGE_ADDITIONAL,
          },
          () => {
            compilation.emitAsset(filename, new compiler.webpack.sources.RawSource(source));
          },
        );
      });
    },
  };
}

module.exports = (_env, argv) => {
  const mode = argv?.mode || process.env.NODE_ENV || "development";
  const isProd = mode === "production";
  const hashLength = 12;
  const swIconSource = fs.readFileSync(path.resolve(__dirname, "public/sw-icon.svg"));
  const swIconHash = crypto.createHash("sha256").update(swIconSource).digest("hex").slice(0, hashLength);
  const swIconFilename = isProd ? `assets/sw-icon.${swIconHash}.svg` : "sw-icon.svg";
  const swIconHref = `/${swIconFilename}`;

  return {
    entry: "./src/index.tsx",
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: isProd ? `assets/[name].[contenthash:${hashLength}].js` : "[name].js",
      chunkFilename: isProd ? `assets/[name].[contenthash:${hashLength}].chunk.js` : "[name].chunk.js",
      assetModuleFilename: isProd ? `assets/[name].[contenthash:${hashLength}][ext][query]` : "assets/[name][ext][query]",
      publicPath: "/",
      clean: true,
    },
    resolve: {
      extensions: [".ts", ".tsx", ".js", ".jsx"],
    },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          use: "ts-loader",
          exclude: /node_modules/,
        },
        {
          test: /\.css$/,
          use: [isProd ? MiniCssExtractPlugin.loader : "style-loader", "css-loader"],
        },
        {
          test: /\.(png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|eot)$/i,
          type: "asset/resource",
        },
      ],
    },
    optimization: {
      chunkIds: isProd ? "deterministic" : "named",
      moduleIds: isProd ? "deterministic" : "named",
      realContentHash: isProd,
    },
    plugins: [
      new HtmlWebpackPlugin({
        filename: "index.html",
        template: "./public/index.html",
        templateParameters: {
          swIconHref,
        },
        title: "Shotwright",
      }),
      new webpack.DefinePlugin({
        __SHOTWRIGHT_DIRECT_API_ORIGIN__: JSON.stringify(isProd ? "" : apiProxyTarget),
        __SHOTWRIGHT_SW_ICON_URL__: JSON.stringify(swIconHref),
      }),
      createStaticAssetPlugin("ShotwrightIconAssetPlugin", swIconFilename, swIconSource),
      ...(isProd
        ? [
            new MiniCssExtractPlugin({
              filename: `assets/[name].[contenthash:${hashLength}].css`,
              chunkFilename: `assets/[name].[contenthash:${hashLength}].chunk.css`,
            }),
          ]
        : []),
    ],
    devServer: {
      host: "0.0.0.0",
      port: devServerPort,
      hot: true,
      historyApiFallback: true,
      proxy: [
        {
          context: ["/api"],
          target: apiProxyTarget,
          changeOrigin: true,
        },
      ],
    },
    devtool: isProd ? "source-map" : "eval-source-map",
  };
};
