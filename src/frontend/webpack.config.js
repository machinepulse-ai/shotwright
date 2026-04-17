const path = require("path");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

const isProd = process.env.NODE_ENV === "production";

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
    ...(isProd ? [new MiniCssExtractPlugin({ filename: "[name].[contenthash].css" })] : []),
  ],
  devServer: {
    port: 3000,
    hot: true,
    historyApiFallback: true,
    proxy: [
      {
        context: ["/api"],
        target: "http://backend:8000",
        changeOrigin: true,
      },
    ],
  },
  devtool: isProd ? "source-map" : "eval-source-map",
};
