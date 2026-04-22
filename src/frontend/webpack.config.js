const path = require("path");
const webpack = require("webpack");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

const isProd = process.env.NODE_ENV === "production";
const devServerPort = Number(process.env.PORT || 3000);
const apiProxyTarget = process.env.SHOTWRIGHT_API_PROXY_TARGET || "http://127.0.0.1:8000";

module.exports = {
  entry: "./src/index.tsx",
  output: {
    path: path.resolve(__dirname, "dist"),
    filename: isProd ? "[name].[contenthash].js" : "[name].js",
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
    ],
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: "./public/index.html",
      title: "Shotwright",
    }),
    new webpack.DefinePlugin({
      __SHOTWRIGHT_DIRECT_API_ORIGIN__: JSON.stringify(isProd ? "" : apiProxyTarget),
    }),
    ...(isProd ? [new MiniCssExtractPlugin({ filename: "[name].[contenthash].css" })] : []),
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
