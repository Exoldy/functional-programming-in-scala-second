import { defineConfig } from 'vitepress'
import sidebar from './sidebar.mjs'
import sidebarRu from './sidebar-ru.mjs'

export default defineConfig({
  title: 'FP in Scala',
  description: 'Page-by-page Markdown conversion of the source PDF',
  base: '/functional-programming-in-scala-second/',
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      themeConfig: {
        siteTitle: 'FP in Scala',
        nav: [
          { text: 'Home', link: '/' },
          { text: 'RU', link: '/ru/' }
        ],
        sidebar
      }
    },
    ru: {
      label: 'Русский',
      lang: 'ru-RU',
      title: 'FP на Scala',
      description: 'Постраничный перевод и разметка книги Functional Programming in Scala, Second Edition',
      themeConfig: {
        siteTitle: 'FP на Scala',
        nav: [
          { text: 'Главная', link: '/ru/' },
          { text: 'EN', link: '/' }
        ],
        sidebar: sidebarRu
      }
    }
  }
})
