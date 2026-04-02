import sidebar from './sidebar.mjs'

export default {
  title: 'Functional Programming in Scala',
  description: 'Page-by-page Markdown conversion of the source PDF',
  base: '/functional-programming-in-scala-second/',
  themeConfig: {
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Pages', link: '/pages/' }
    ],
    sidebar
  }
}

