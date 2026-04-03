import { defineConfig } from 'vitepress'
import sidebar from './sidebar.mjs'
import sidebarRu from './sidebar-ru.mjs'

export default defineConfig({
  title: 'Functional Programming in Scala',
  description: 'Page-by-page Markdown conversion of the source PDF',
  base: '/functional-programming-in-scala-second/',
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      themeConfig: {
        nav: [
          { text: 'Home', link: '/' },
          { text: 'Pages', link: '/pages/' },
          { text: 'Russian', link: '/ru/' }
        ],
        sidebar
      }
    },
    ru: {
      label: 'Русский',
      lang: 'ru-RU',
      title: 'Функциональное программирование в Scala',
      description: 'Постраничный перевод и разметка книги Functional Programming in Scala, Second Edition',
      themeConfig: {
        nav: [
          { text: 'Главная', link: '/ru/' },
          { text: 'Страницы', link: '/ru/pages/' },
          { text: 'English', link: '/' }
        ],
        sidebar: sidebarRu
      }
    }
  }
})
